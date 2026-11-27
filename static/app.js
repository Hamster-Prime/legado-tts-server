(() => {
    'use strict';

    const INITIAL = window.__TTS_INITIAL__ || {};
    const PROVIDERS = {
        edge: {name: 'Edge TTS', short: 'Edge', credentialTitle: '服务凭证'},
        doubao: {name: '火山引擎', short: '火山', credentialTitle: '火山引擎凭证'},
        tencent: {name: '腾讯云 TTS', short: '腾讯云', credentialTitle: '腾讯云凭证'},
        xiaomi: {name: '小米 MiMo', short: 'MiMo', credentialTitle: '小米 MiMo 凭证'},
        fishaudio: {name: 'Fish Audio', short: 'Fish Audio', credentialTitle: 'Fish Audio 凭证'},
    };
    const VOICE_FIELDS = {
        edge: 'edge_voice',
        doubao: 'default_voice',
        tencent: 'tencent_voice',
        xiaomi: 'xiaomi_voice',
        fishaudio: 'fishaudio_voice',
    };
    const PROVIDER_CREDENTIALS = {
        edge: [],
        doubao: ['doubao_api_key'],
        tencent: ['tencent_secret_id', 'tencent_secret_key'],
        xiaomi: ['xiaomi_api_key'],
        fishaudio: ['fishaudio_api_key'],
    };
    const CUSTOM_VOICE_PROVIDERS = new Set(['doubao', 'fishaudio']);
    const CUSTOM_VOICE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
    const PROVIDER_BUTTON_SELECTOR = '.provider-option, .provider-item';
    const FALLBACK_DEFAULTS = Object.freeze({
        enabled: true,
        voice: 'zh-CN-XiaoxiaoNeural',
        retryCount: 1,
        retryDelayMs: 500,
    });
    const FALLBACK_LIMITS = Object.freeze({
        retryCount: {min: 0, max: 3},
        retryDelayMs: {min: 0, max: 5000},
    });

    const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
    function camelConfigKey(key) {
        return key.replace(/_([a-z])/g, (_, letter) => letter.toUpperCase());
    }

    function initialConfigValue(source, key, fallback) {
        const camelKey = camelConfigKey(key);
        const sources = [source, source && source.fallback];
        for (const candidate of sources) {
            if (!candidate || typeof candidate !== 'object') continue;
            if (hasOwn(candidate, key)) return candidate[key];
            if (hasOwn(candidate, camelKey)) return candidate[camelKey];
        }
        return fallback;
    }

    function hasInitialConfigValue(source, key) {
        const camelKey = camelConfigKey(key);
        return [source, source && source.fallback].some(candidate => (
            candidate && typeof candidate === 'object'
            && (hasOwn(candidate, key) || hasOwn(candidate, camelKey))
        ));
    }

    function hasInitialFallbackSettings() {
        return [
            'fallback_enabled',
            'fallback_voice',
            'fallback_retry_count',
            'fallback_retry_delay_ms',
        ].every(key => hasInitialConfigValue(INITIAL, key));
    }

    function initialBoolean(value, fallback) {
        if (typeof value === 'boolean') return value;
        if (typeof value === 'number') return value !== 0;
        if (typeof value === 'string') {
            const normalized = value.trim().toLowerCase();
            if (['0', 'false', 'off', 'no'].includes(normalized)) return false;
            if (['1', 'true', 'on', 'yes'].includes(normalized)) return true;
        }
        return fallback;
    }

    function initialInteger(value, fallback, limits) {
        const parsed = Number(value);
        if (!Number.isInteger(parsed)) return fallback;
        return Math.min(limits.max, Math.max(limits.min, parsed));
    }

    const invalidInitialProvider = Boolean(INITIAL.provider && !hasOwn(PROVIDERS, INITIAL.provider));
    const initialProvider = invalidInitialProvider ? 'edge' : (INITIAL.provider || 'edge');
    const state = {
        provider: initialProvider,
        savedProvider: initialProvider,
        selectedVoices: Object.assign({
            edge: 'zh-CN-XiaoxiaoNeural',
            doubao: 'zh_female_cancan_uranus_bigtts',
            tencent: '501002',
            xiaomi: 'mimo_default',
            fishaudio: 'fish-default',
        }, INITIAL.voices || {}),
        providerStatus: INITIAL.providerStatus || {},
        configuredFields: INITIAL.configuredFields || {},
        voiceCatalogs: {},
        voiceTouched: new Set(),
        customVoiceDrafts: {},
        voiceLoading: false,
        customVoiceBusy: false,
        stats: {},
        baselineCore: '',
        dirty: false,
        needsProviderRepair: invalidInitialProvider,
        fallbackVoiceLoading: false,
        voiceRequestIds: {},
        configRequestId: 0,
        legadoConfigText: '',
        authPrompted: false,
        activitySource: null,
        activityAudio: null,
        activityAudioUrl: '',
        monitorTimer: null,
        modalAudio: null,
        modalAudioUrl: '',
        modalPreviewId: 0,
        modalPreviewButton: null,
        modalPreviewVoiceId: '',
        previewRequestId: 0,
        testRequestId: 0,
        compareRequestId: 0,
        initialized: false,
    };

    const $ = id => document.getElementById(id);
    const $$ = selector => Array.from(document.querySelectorAll(selector));

    function providerMeta(provider = state.provider) {
        return PROVIDERS[provider] || PROVIDERS.edge;
    }

    function getToken() {
        try {
            return localStorage.getItem('tts-admin-token') || '';
        } catch (_) {
            return '';
        }
    }

    async function errorText(response) {
        try {
            const data = await response.clone().json();
            if (typeof data.error === 'string') return data.error;
            if (data.error && data.error.message) return data.error.message;
            if (data.message) return data.message;
        } catch (_) {
            try {
                const text = (await response.text()).trim();
                if (text) return text;
            } catch (_) {}
        }
        return response.statusText || `HTTP ${response.status}`;
    }

    async function api(url, options = {}) {
        const headers = new Headers(options.headers || {});
        const token = getToken();
        if (token) headers.set('Authorization', `Bearer ${token}`);
        let response;
        try {
            response = await fetch(url, Object.assign({}, options, {headers}));
        } catch (error) {
            throw new Error(error && error.message ? `无法连接服务器：${error.message}` : '无法连接服务器');
        }
        if (response.status === 401 && !state.authPrompted) {
            state.authPrompted = true;
            notify('管理令牌无效或尚未设置', 'warning', 5000);
            window.setTimeout(() => setAdminToken(), 120);
        }
        return response;
    }

    async function expectOk(response) {
        if (!response.ok) throw new Error(await errorText(response));
        return response;
    }

    async function jsonRequest(url, options) {
        const response = await expectOk(await api(url, options));
        try {
            return await response.json();
        } catch (_) {
            throw new Error('服务器返回了无效数据');
        }
    }

    function createSvgIcon(symbolId, className = 'icon') {
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', className);
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', `#${symbolId}`);
        svg.appendChild(use);
        return svg;
    }

    function notify(message, type = 'info', duration = 3500) {
        const region = $('toast-region');
        if (!region) return;
        const toast = document.createElement('div');
        const icons = {
            success: 'icon-check',
            warning: 'icon-alert',
            error: 'icon-x-circle',
            info: 'icon-info',
        };
        toast.className = `toast is-${type}`;
        toast.style.setProperty('--toast-duration', `${duration}ms`);
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

        const icon = document.createElement('span');
        icon.className = 'toast-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.appendChild(createSvgIcon(icons[type] || icons.info));

        const text = document.createElement('span');
        text.className = 'toast-message';
        text.textContent = String(message || '');

        const close = document.createElement('button');
        close.className = 'toast-close';
        close.type = 'button';
        close.setAttribute('aria-label', '关闭通知');
        close.appendChild(createSvgIcon('icon-close'));

        let timer = null;
        let remaining = duration;
        let startedAt = Date.now();
        let closed = false;

        const remove = () => {
            if (closed) return;
            closed = true;
            if (timer) window.clearTimeout(timer);
            toast.classList.remove('is-visible');
            window.setTimeout(() => toast.remove(), 260);
        };
        const scheduleClose = delay => {
            if (timer) window.clearTimeout(timer);
            startedAt = Date.now();
            timer = window.setTimeout(remove, Math.max(250, delay));
        };

        toast.addEventListener('mouseenter', () => {
            if (timer) {
                window.clearTimeout(timer);
                timer = null;
                remaining = Math.max(400, remaining - (Date.now() - startedAt));
            }
        });
        toast.addEventListener('mouseleave', () => {
            if (!closed && !timer) scheduleClose(remaining);
        });
        close.addEventListener('click', remove);
        toast.append(icon, text, close);
        region.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('is-visible'));
        scheduleClose(duration);
        return toast;
    }

    let activeSynthCount = 0;
    function setSynthesizing(delta) {
        activeSynthCount = Math.max(0, activeSynthCount + delta);
        if (document.body) document.body.classList.toggle('is-synthesizing', activeSynthCount > 0);
    }

    function formatDuration(seconds) {
        if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
        const total = Math.floor(seconds);
        const mins = Math.floor(total / 60);
        const secs = total % 60;
        return `${mins}:${String(secs).padStart(2, '0')}`;
    }

    function pauseOtherAudio(exceptElement) {
        $$('audio.audio-native').forEach(audio => {
            if (audio !== exceptElement && !audio.paused) audio.pause();
        });
        if (state.modalAudio && state.modalAudio !== exceptElement && !state.modalAudio.paused) {
            state.modalAudio.pause();
        }
    }

    function syncPlayerUI(container, audio) {
        if (!container || !audio) return;
        const duration = Number.isFinite(audio.duration) ? audio.duration : 0;
        const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;
        const ratio = duration > 0 ? Math.min(1, Math.max(0, current / duration)) : 0;
        const seek = container.querySelector('.player-seek');
        const fill = container.querySelector('[data-player-fill]');
        const currentEl = container.querySelector('[data-player-current]');
        const durationEl = container.querySelector('[data-player-duration]');
        if (seek && document.activeElement !== seek) seek.value = String(Math.round(ratio * 1000));
        if (fill) fill.style.setProperty('--progress', `${(ratio * 100).toFixed(2)}%`);
        if (currentEl) currentEl.textContent = formatDuration(current);
        if (durationEl) durationEl.textContent = formatDuration(duration);
    }

    function initStudioPlayers() {
        $$('.studio-player[data-player-for]').forEach(container => {
            const audio = $(container.dataset.playerFor);
            if (!audio || container.dataset.bound === 'true') return;
            container.dataset.bound = 'true';
            const playBtn = container.querySelector('.player-play-btn');
            const seek = container.querySelector('.player-seek');
            const fill = container.querySelector('[data-player-fill]');

            if (playBtn) {
                playBtn.addEventListener('click', () => {
                    if (!audio.src) return;
                    if (audio.paused) {
                        pauseOtherAudio(audio);
                        audio.play().catch(() => {});
                    } else {
                        audio.pause();
                    }
                });
            }
            if (seek) {
                seek.addEventListener('input', () => {
                    const ratio = Number(seek.value) / 1000;
                    if (fill) fill.style.setProperty('--progress', `${(ratio * 100).toFixed(2)}%`);
                    if (Number.isFinite(audio.duration) && audio.duration > 0) {
                        audio.currentTime = ratio * audio.duration;
                    }
                });
            }
            audio.addEventListener('play', () => {
                pauseOtherAudio(audio);
                container.classList.add('is-playing');
                if (playBtn) playBtn.setAttribute('aria-label', '暂停音频');
            });
            audio.addEventListener('pause', () => {
                container.classList.remove('is-playing');
                if (playBtn) playBtn.setAttribute('aria-label', '播放音频');
            });
            audio.addEventListener('ended', () => {
                container.classList.remove('is-playing');
                if (playBtn) playBtn.setAttribute('aria-label', '重新播放音频');
                syncPlayerUI(container, audio);
            });
            ['loadedmetadata', 'durationchange', 'timeupdate'].forEach(evt => {
                audio.addEventListener(evt, () => syncPlayerUI(container, audio));
            });
        });
    }

    function getCollapseShell(target) {
        if (!target) return null;
        if (target.classList && target.classList.contains('collapse-shell')) return target;
        return typeof target.closest === 'function' ? target.closest('.collapse-shell') : null;
    }

    function isCollapseOpen(target) {
        const shell = getCollapseShell(target);
        if (shell) return shell.classList.contains('is-open');
        return Boolean(target && !target.hidden);
    }

    function setCollapseOpen(target, open) {
        if (!target) return;
        const shell = getCollapseShell(target);
        if (!shell) {
            target.hidden = !open;
            return;
        }
        if (open) {
            target.hidden = false;
            shell.removeAttribute('inert');
            shell.setAttribute('aria-hidden', 'false');
            if (!shell.classList.contains('is-open')) {
                void shell.offsetHeight;
                shell.classList.add('is-open');
            }
        } else {
            shell.classList.remove('is-open');
            shell.setAttribute('aria-hidden', 'true');
            shell.setAttribute('inert', '');
        }
    }

    function setFeedback(idOrElement, message = '', type = 'info') {
        const element = typeof idOrElement === 'string' ? $(idOrElement) : idOrElement;
        if (!element) return;
        if (element._collapseClearTimer) {
            window.clearTimeout(element._collapseClearTimer);
            element._collapseClearTimer = null;
        }
        const shell = getCollapseShell(element);
        if (!shell) {
            element.className = `inline-feedback${message ? ` is-${type}` : ''}`;
            element.textContent = message;
            element.hidden = !message;
            return;
        }
        if (message) {
            element.className = `inline-feedback is-${type}`;
            element.textContent = message;
            setCollapseOpen(shell, true);
        } else {
            setCollapseOpen(shell, false);
            element._collapseClearTimer = window.setTimeout(() => {
                element._collapseClearTimer = null;
                if (!shell.classList.contains('is-open')) {
                    element.textContent = '';
                    element.className = 'inline-feedback';
                }
            }, 300);
        }
    }

    function setButtonBusy(button, busy, busyText) {
        if (!button) return;
        const label = button.querySelector('.btn-label');
        if (busy) {
            if (!button.dataset.idleText) button.dataset.idleText = (label || button).textContent.trim();
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            if (busyText) (label || button).textContent = busyText;
        } else {
            button.removeAttribute('aria-busy');
            if (button.dataset.idleText) (label || button).textContent = button.dataset.idleText;
            button.disabled = false;
        }
    }

    function setAudioBlob(element, blob, title = '') {
        if (!element) return;
        if (element.dataset.blobUrl) URL.revokeObjectURL(element.dataset.blobUrl);
        const url = URL.createObjectURL(blob);
        element.dataset.blobUrl = url;
        element.src = url;
        element.hidden = false;
        const container = typeof element.closest === 'function' ? element.closest('.studio-player') : null;
        if (container) {
            container.hidden = false;
            container.classList.remove('is-playing');
            const titleEl = container.querySelector('[data-player-title]');
            if (titleEl && title) titleEl.textContent = title;
            const downloadLink = container.querySelector('[data-player-download]');
            if (downloadLink) downloadLink.href = url;
            syncPlayerUI(container, element);
            setCollapseOpen(container, true);
            container.classList.remove('is-fresh');
            void container.offsetWidth;
            container.classList.add('is-fresh');
        }
    }

    function resetAudioElement(element) {
        if (!element) return;
        element.pause();
        if (element.dataset.blobUrl) URL.revokeObjectURL(element.dataset.blobUrl);
        delete element.dataset.blobUrl;
        element.removeAttribute('src');
        element.load();
        element.hidden = true;
        const container = typeof element.closest === 'function' ? element.closest('.studio-player') : null;
        if (container) {
            container.classList.remove('is-playing', 'is-fresh');
            const shell = getCollapseShell(container);
            if (shell) setCollapseOpen(shell, false);
            else container.hidden = true;
            const fill = container.querySelector('[data-player-fill]');
            const seek = container.querySelector('.player-seek');
            const currentEl = container.querySelector('[data-player-current]');
            const durationEl = container.querySelector('[data-player-duration]');
            const downloadLink = container.querySelector('[data-player-download]');
            if (fill) fill.style.setProperty('--progress', '0%');
            if (seek) seek.value = '0';
            if (currentEl) currentEl.textContent = '0:00';
            if (durationEl) durationEl.textContent = '0:00';
            if (downloadLink) downloadLink.removeAttribute('href');
        }
    }

    function clearSynthesisOutputs() {
        state.previewRequestId += 1;
        state.testRequestId += 1;
        state.compareRequestId += 1;
        ['preview-button', 'test-tts-button', 'compare-button'].forEach(id => {
            const button = $(id);
            if (button && button.hasAttribute('aria-busy')) setButtonBusy(button, false);
        });
        ['preview-player', 'test-audio-player', 'audio-a', 'audio-b'].forEach(id => resetAudioElement($(id)));
        ['voice-feedback', 'test-feedback', 'compare-feedback'].forEach(id => setFeedback(id));
    }

    async function playAudio(element, feedbackId) {
        try {
            pauseOtherAudio(element);
            await element.play();
            return true;
        } catch (_) {
            setFeedback(feedbackId, '音频已生成；浏览器阻止了自动播放，请点击播放器手动播放。', 'warning');
            return false;
        }
    }

    async function copyText(text) {
        if (!text) throw new Error('没有可复制的内容');
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return;
            } catch (_) {}
        }
        const area = document.createElement('textarea');
        area.value = text;
        area.setAttribute('readonly', '');
        area.style.position = 'fixed';
        area.style.left = '-9999px';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        area.setSelectionRange(0, area.value.length);
        let copied = false;
        try {
            copied = document.execCommand('copy');
        } finally {
            area.remove();
        }
        if (!copied) throw new Error('浏览器不允许复制，请手动选择内容');
    }

    const THEME_MODES = new Set(['auto', 'white', 'light', 'dark']);
    const THEME_LABELS = {auto: '自动', white: '纯白', light: '暖浅', dark: '深炭'};
    const THEME_ICONS = {
        auto: '#icon-theme-auto',
        white: '#icon-theme-white',
        light: '#icon-sun',
        dark: '#icon-moon',
    };

    function getThemeMode() {
        try {
            const saved = localStorage.getItem('tts-theme') || 'auto';
            if (THEME_MODES.has(saved)) return saved;
        } catch (_) {}
        const fromDom = document.documentElement.dataset.themeMode;
        return THEME_MODES.has(fromDom) ? fromDom : 'auto';
    }

    function resolveTheme(mode) {
        if (mode === 'auto') {
            const prefersDark = typeof window.matchMedia === 'function'
                && window.matchMedia('(prefers-color-scheme: dark)').matches;
            return prefersDark ? 'dark' : 'light';
        }
        return THEME_MODES.has(mode) ? mode : 'light';
    }

    function applyThemeMode(mode, persist = true) {
        const validMode = THEME_MODES.has(mode) ? mode : 'auto';
        const resolved = resolveTheme(validMode);
        document.documentElement.dataset.themeMode = validMode;
        document.documentElement.dataset.theme = resolved;
        document.documentElement.setAttribute('data-theme', resolved);
        if (persist) {
            try { localStorage.setItem('tts-theme', validMode); } catch (_) {}
        }
        updateThemeButton();
    }

    function updateThemeButton() {
        const mode = getThemeMode();
        const resolved = document.documentElement.dataset.theme || resolveTheme(mode);
        if ($('theme-icon-use')) {
            $('theme-icon-use').setAttribute('href', THEME_ICONS[mode] || THEME_ICONS.auto);
        }
        if ($('theme-label')) {
            $('theme-label').textContent = THEME_LABELS[mode] || '自动';
        }
        if ($('theme-button')) {
            $('theme-button').setAttribute('aria-label', `切换外观主题（当前：${THEME_LABELS[mode] || resolved}）`);
        }
        $$('[data-theme-option]').forEach(card => {
            const active = card.dataset.themeOption === mode;
            card.classList.toggle('active', active);
            card.setAttribute('aria-checked', String(active));
        });
    }

    function setThemeMenuOpen(open) {
        const menu = $('theme-menu');
        const popover = $('theme-menu-popover');
        const button = $('theme-button');
        if (!menu || !popover || !button) return;
        const willOpen = Boolean(open);
        menu.classList.toggle('open', willOpen);
        popover.hidden = !willOpen;
        button.setAttribute('aria-expanded', String(willOpen));
    }

    function toggleTheme() {
        const popover = $('theme-menu-popover');
        if (popover) {
            setThemeMenuOpen(popover.hidden);
            return;
        }
        const dark = document.documentElement.getAttribute('data-theme') === 'dark'
            || document.documentElement.dataset.theme === 'dark';
        applyThemeMode(dark ? 'light' : 'dark');
    }

    function openDialogElement(dialog) {
        if (!dialog) return;
        if (typeof dialog.showModal === 'function') {
            if (!dialog.open) dialog.showModal();
        } else {
            dialog.setAttribute('open', '');
        }
    }

    function closeDialogElement(dialog) {
        if (!dialog) return;
        if (dialog.open && typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
    }

    function confirmAction({
        title = '请确认操作',
        message = '确定继续吗？',
        confirmText = '确认',
        cancelText = '取消',
        tone = 'danger',
    } = {}) {
        const dialog = $('confirm-dialog');
        if (!dialog) return Promise.resolve(window.confirm(message));
        $('confirm-dialog-title').textContent = title;
        $('confirm-dialog-message').textContent = message;
        const okBtn = $('confirm-dialog-ok');
        const cancelBtn = $('confirm-dialog-cancel');
        const closeBtn = $('confirm-dialog-close');
        const iconWrap = $('confirm-dialog-icon');
        okBtn.textContent = confirmText;
        okBtn.className = `btn ${tone === 'danger' ? 'btn-danger' : 'btn-primary'}`;
        cancelBtn.textContent = cancelText;
        if (iconWrap) {
            iconWrap.className = `confirm-icon${tone === 'warning' ? ' is-warning' : (tone === 'primary' ? ' is-accent' : '')}`;
        }
        return new Promise(resolve => {
            let settled = false;
            const finish = result => {
                if (settled) return;
                settled = true;
                okBtn.removeEventListener('click', onOk);
                cancelBtn.removeEventListener('click', onCancel);
                closeBtn.removeEventListener('click', onCancel);
                dialog.removeEventListener('cancel', onCancel);
                dialog.removeEventListener('click', onBackdrop);
                closeDialogElement(dialog);
                resolve(result);
            };
            const onOk = () => finish(true);
            const onCancel = event => {
                if (event && event.type === 'cancel') event.preventDefault();
                finish(false);
            };
            const onBackdrop = event => {
                const rect = dialog.getBoundingClientRect();
                const outside = event.clientX < rect.left || event.clientX > rect.right
                    || event.clientY < rect.top || event.clientY > rect.bottom;
                if (outside) finish(false);
            };
            okBtn.addEventListener('click', onOk);
            cancelBtn.addEventListener('click', onCancel);
            closeBtn.addEventListener('click', onCancel);
            dialog.addEventListener('cancel', onCancel);
            dialog.addEventListener('click', onBackdrop);
            openDialogElement(dialog);
            window.setTimeout(() => okBtn.focus(), 20);
        });
    }

    function saveAdminTokenValue(rawValue) {
        const token = String(rawValue || '').trim();
        try {
            if (token) localStorage.setItem('tts-admin-token', token);
            else localStorage.removeItem('tts-admin-token');
        } catch (_) {
            notify('浏览器无法保存管理令牌', 'error');
            return;
        }
        state.authPrompted = false;
        notify(token ? '管理令牌已更新' : '管理令牌已移除', 'success');
        window.setTimeout(() => window.location.reload(), 280);
    }

    async function setAdminToken() {
        const dialog = $('token-dialog');
        const input = $('token-dialog-input');
        const current = getToken();
        if (!dialog || !input) {
            const value = window.prompt('请输入管理令牌（留空将移除当前浏览器中的令牌）', current);
            if (value === null) return;
            saveAdminTokenValue(value);
            return;
        }
        input.value = current;
        input.type = 'password';
        const toggle = dialog.querySelector('[data-toggle-secret="token-dialog-input"]');
        if (toggle) {
            toggle.setAttribute('aria-pressed', 'false');
            const use = toggle.querySelector('use');
            if (use) use.setAttribute('href', '#icon-eye');
        }
        const clearBtn = $('token-dialog-clear');
        if (clearBtn) clearBtn.hidden = !current;
        openDialogElement(dialog);
        window.setTimeout(() => {
            input.focus();
            input.select();
        }, 30);
    }

    function syncTabIndicator(container, activeTab) {
        if (!container || !activeTab) return;
        const indicator = container.querySelector('.tab-indicator');
        if (!indicator) return;
        const width = activeTab.offsetWidth;
        const left = activeTab.offsetLeft;
        if (!width) return;
        const firstPaint = !container.classList.contains('has-indicator');
        indicator.style.width = `${width}px`;
        indicator.style.transform = `translateX(${left}px)`;
        if (firstPaint) void indicator.offsetWidth;
        container.classList.add('has-indicator');
    }

    function syncAllTabIndicators() {
        const navContainer = $('console-nav-tabs');
        const activeNav = navContainer && navContainer.querySelector('.nav-tab-btn.is-active');
        if (navContainer && activeNav) syncTabIndicator(navContainer, activeNav);
        const subContainer = $('studio-sub-tabs');
        const activeSub = subContainer && subContainer.querySelector('.sub-tab-btn.is-active');
        if (subContainer && activeSub) syncTabIndicator(subContainer, activeSub);
    }

    function readSectionPreferences() {
        try {
            const value = JSON.parse(localStorage.getItem('tts-ui-sections') || '{}');
            return value && typeof value === 'object' ? value : {};
        } catch (_) {
            return {};
        }
    }

    function saveSectionPreference(name, open) {
        try {
            const preferences = readSectionPreferences();
            preferences[name] = open;
            localStorage.setItem('tts-ui-sections', JSON.stringify(preferences));
        } catch (_) {}
    }

    function isSectionOpen(name) {
        const panel = document.querySelector(`[data-section="${name}"]`);
        return Boolean(panel && panel.classList.contains('is-open'));
    }

    function isMonitoringVisible() {
        const main = document.querySelector('[data-console-page="main"]');
        if (main && main.hidden) return false;
        const logs = $('sub-panel-logs');
        return logs ? !logs.hidden && logs.style.display !== 'none' : isSectionOpen('monitoring');
    }

    function setSectionOpen(panel, open, persist = true, runEffects = true) {
        const toggle = panel.querySelector('.panel-toggle');
        const content = panel.querySelector('.panel-collapse');
        const name = panel.dataset.section;
        if (!open && content && content.contains(document.activeElement)) toggle.focus();
        panel.classList.toggle('is-open', open);
        toggle.setAttribute('aria-expanded', String(open));
        if (content) {
            content.setAttribute('aria-hidden', String(!open));
            content.inert = !open;
        }
        if (persist) saveSectionPreference(name, open);
        if (!runEffects) return;
        if (name === 'monitoring') {
            if (open) startMonitoring();
            else stopMonitoring();
        }
    }

    function initCollapsibles() {
        const preferences = readSectionPreferences();
        $$('.collapsible').forEach(panel => {
            const name = panel.dataset.section;
            const defaultOpen = name === 'setup' || name === 'voice-tools';
            const open = hasOwn(preferences, name) ? Boolean(preferences[name]) : defaultOpen;
            setSectionOpen(panel, open, false, false);
            panel.querySelector('.panel-toggle').addEventListener('click', () => {
                setSectionOpen(panel, !panel.classList.contains('is-open'));
            });
        });
    }

    function syncFloatingSaveBar() {
        const bar = $('floating-save-bar');
        if (!bar) return;
        const mainPage = document.querySelector('[data-console-page="main"]');
        const onMain = !mainPage || !mainPage.hidden;
        bar.hidden = !(state.dirty && onMain);
        document.body.classList.toggle('has-floating-save', !bar.hidden);
    }

    function updateBackToTopVisibility() {
        const btn = $('back-to-top-btn');
        if (!btn) return;
        const scroller = $('content-scroll');
        const y = Math.max(scroller ? scroller.scrollTop : 0, window.scrollY || document.documentElement.scrollTop || 0);
        const visible = y >= 140;
        btn.classList.toggle('is-visible', visible);
        btn.setAttribute('aria-hidden', String(!visible));
        btn.tabIndex = visible ? 0 : -1;
    }

    function initBackToTop() {
        const btn = $('back-to-top-btn');
        if (!btn) return;
        const scroller = $('content-scroll');
        const onScroll = () => updateBackToTopVisibility();
        if (scroller) scroller.addEventListener('scroll', onScroll, {passive: true});
        window.addEventListener('scroll', onScroll, {passive: true});
        window.addEventListener('resize', onScroll, {passive: true});
        btn.addEventListener('click', () => {
            const reduced = typeof window.matchMedia === 'function'
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            const behavior = reduced ? 'auto' : 'smooth';
            if (scroller && typeof scroller.scrollTo === 'function') {
                scroller.scrollTo({top: 0, behavior});
            }
            if (typeof window.scrollTo === 'function') {
                window.scrollTo({top: 0, behavior});
            }
        });
        updateBackToTopVisibility();
    }

    function setMobileSidebarOpen(open) {
        const sidebar = $('app-sidebar');
        const backdrop = $('sidebar-backdrop');
        const btn = $('mobile-menu-btn');
        const iconUse = $('mobile-menu-icon-use');
        if (!sidebar) return;
        const willOpen = Boolean(open);
        sidebar.classList.toggle('open', willOpen);
        if (backdrop) {
            backdrop.classList.toggle('visible', willOpen);
            backdrop.setAttribute('aria-hidden', String(!willOpen));
            backdrop.tabIndex = willOpen ? 0 : -1;
        }
        if (btn) {
            btn.setAttribute('aria-expanded', String(willOpen));
            btn.setAttribute('aria-label', willOpen ? '关闭导航菜单' : '打开导航菜单');
        }
        if (iconUse) iconUse.setAttribute('href', willOpen ? '#icon-close' : '#icon-menu');
    }

    function applySidebarCollapsed(collapsed, persist = true) {
        const shell = $('app-shell');
        const sidebar = $('app-sidebar');
        const toggleBtn = $('sidebar-toggle-btn');
        const toggleUse = $('sidebar-toggle-icon-use');
        const isCollapsed = Boolean(collapsed);
        document.documentElement.classList.remove('sidebar-collapsed-init');
        if (shell) shell.classList.toggle('sidebar-is-collapsed', isCollapsed);
        if (sidebar) sidebar.classList.toggle('collapsed', isCollapsed);
        if (toggleBtn) {
            const label = isCollapsed ? '展开侧边栏' : '折叠侧边栏';
            toggleBtn.setAttribute('aria-label', `${label} (Ctrl+B)`);
            toggleBtn.dataset.tooltipLabel = label;
        }
        if (toggleUse) {
            toggleUse.setAttribute('href', isCollapsed ? '#icon-chevron' : '#icon-chevron-left');
        }
        if (persist) {
            try { localStorage.setItem('tts-sidebar-collapsed', isCollapsed ? '1' : '0'); } catch (_) {}
        }
        hideSidebarTooltip();
        requestAnimationFrame(syncContentCenterX);
    }

    function syncContentCenterX() {
        const scroller = $('content-scroll');
        if (!scroller) return;
        const rect = scroller.getBoundingClientRect();
        if (!rect.width) return;
        document.documentElement.style.setProperty('--content-center-x', `${rect.left + rect.width / 2}px`);
    }

    function showSidebarTooltip(target) {
        const tip = $('sidebar-nav-tooltip');
        const shell = $('app-shell');
        if (!tip || !target) return;
        const isToggle = target.id === 'sidebar-toggle-btn';
        const isCollapsed = Boolean(shell && shell.classList.contains('sidebar-is-collapsed'));
        if (!isToggle && !isCollapsed) {
            tip.hidden = true;
            return;
        }
        const label = target.dataset.tooltipLabel || '';
        const meta = target.dataset.tooltipMeta || '';
        if (!label) return;
        if ($('sidebar-tooltip-label')) $('sidebar-tooltip-label').textContent = label;
        if ($('sidebar-tooltip-meta')) {
            $('sidebar-tooltip-meta').textContent = meta;
            $('sidebar-tooltip-meta').hidden = !meta;
        }
        const rect = target.getBoundingClientRect();
        const top = Math.max(24, Math.min(window.innerHeight - 24, rect.top + rect.height / 2));
        tip.style.top = `${top}px`;
        tip.hidden = false;
    }

    function hideSidebarTooltip() {
        const tip = $('sidebar-nav-tooltip');
        if (tip) tip.hidden = true;
    }

    function initSidebarController() {
        let initialCollapsed = false;
        try {
            initialCollapsed = localStorage.getItem('tts-sidebar-collapsed') === '1';
        } catch (_) {}
        applySidebarCollapsed(initialCollapsed, false);

        const toggleBtn = $('sidebar-toggle-btn');
        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                const shell = $('app-shell');
                const next = !(shell && shell.classList.contains('sidebar-is-collapsed'));
                applySidebarCollapsed(next);
            });
        }
        const mobileBtn = $('mobile-menu-btn');
        if (mobileBtn) {
            mobileBtn.addEventListener('click', () => {
                const sidebar = $('app-sidebar');
                setMobileSidebarOpen(!(sidebar && sidebar.classList.contains('open')));
            });
        }
        const backdrop = $('sidebar-backdrop');
        if (backdrop) {
            backdrop.addEventListener('click', () => setMobileSidebarOpen(false));
        }

        $$('[data-tooltip-label]').forEach(el => {
            el.addEventListener('mouseenter', () => showSidebarTooltip(el));
            el.addEventListener('mouseleave', hideSidebarTooltip);
            el.addEventListener('focus', () => showSidebarTooltip(el));
            el.addEventListener('blur', hideSidebarTooltip);
        });

        $$('[data-nav-jump]').forEach(btn => {
            btn.addEventListener('click', () => {
                const targetId = btn.dataset.navJump;
                const subtab = btn.dataset.navSubtab;
                setConsolePage('main');
                if (subtab) switchSubTab(subtab);
                setMobileSidebarOpen(false);
                hideSidebarTooltip();
                const targetEl = targetId ? $(targetId) : null;
                if (targetEl && typeof targetEl.scrollIntoView === 'function') {
                    requestAnimationFrame(() => {
                        targetEl.scrollIntoView({behavior: 'smooth', block: 'start'});
                    });
                }
            });
        });

        window.addEventListener('keydown', event => {
            if ((event.metaKey || event.ctrlKey) && (event.key === 'b' || event.key === 'B')) {
                const tag = event.target && event.target.tagName;
                if (tag === 'INPUT' || tag === 'TEXTAREA' || (event.target && event.target.isContentEditable)) return;
                event.preventDefault();
                const shell = $('app-shell');
                applySidebarCollapsed(!(shell && shell.classList.contains('sidebar-is-collapsed')));
            } else if (event.key === 'Escape') {
                setMobileSidebarOpen(false);
                setThemeMenuOpen(false);
            }
        });

        syncContentCenterX();
        window.addEventListener('resize', syncContentCenterX);
        if (typeof ResizeObserver !== 'undefined' && $('content-scroll')) {
            new ResizeObserver(syncContentCenterX).observe($('content-scroll'));
        }
    }

    function setConsolePage(name) {
        const pageName = name === 'maintenance' ? 'maintenance' : 'main';
        $$('[data-console-page]').forEach(page => {
            const active = page.dataset.consolePage === pageName;
            page.hidden = !active;
            page.classList.toggle('is-active', active);
        });
        $$('[data-console-tab]').forEach(tab => {
            const active = tab.dataset.consoleTab === pageName;
            tab.classList.toggle('is-active', active);
            tab.classList.toggle('active', active);
            tab.setAttribute('aria-selected', String(active));
            tab.tabIndex = active ? 0 : -1;
        });
        $$('[data-console-main-only]').forEach(element => {
            element.hidden = pageName !== 'main';
        });

        const maintenancePanel = document.querySelector('[data-section="maintenance"]');
        if (pageName === 'maintenance' && maintenancePanel && !maintenancePanel.classList.contains('is-open')) {
            setSectionOpen(maintenancePanel, true, false);
        }
        if (pageName !== 'main') stopMonitoring();
        else if (state.initialized && isMonitoringVisible()) {
            startMonitoring();
        }
        setMobileSidebarOpen(false);
        syncFloatingSaveBar();
        updateBackToTopVisibility();
        requestAnimationFrame(syncAllTabIndicators);
        requestAnimationFrame(syncContentCenterX);
    }

    function initConsoleNavigation() {
        const tabs = $$('[data-console-tab]');
        tabs.forEach(tab => {
            tab.addEventListener('click', () => setConsolePage(tab.dataset.consoleTab));
            tab.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const current = tabs.indexOf(event.currentTarget);
                let next = event.key === 'ArrowLeft' ? current - 1 : current + 1;
                if (event.key === 'Home') next = 0;
                if (event.key === 'End') next = tabs.length - 1;
                const target = tabs[(next + tabs.length) % tabs.length];
                setConsolePage(target.dataset.consoleTab);
                target.focus();
            });
        });
        setConsolePage('main');
    }

    function switchSubTab(sub) {
        ['test', 'compare', 'logs'].forEach(t => {
            const panel = $(`sub-panel-${t}`);
            const btn = $(`btn-sub-${t}`);
            const isActive = t === sub;
            if (panel) {
                panel.hidden = !isActive;
                panel.style.display = isActive ? 'flex' : 'none';
                panel.setAttribute('aria-hidden', String(!isActive));
                panel.classList.remove('fade-in');
                void panel.offsetWidth;
                panel.classList.add('fade-in');
            }
            if (btn) {
                btn.classList.toggle('active', isActive);
                btn.classList.toggle('is-active', isActive);
                btn.setAttribute('aria-selected', String(isActive));
                btn.tabIndex = isActive ? 0 : -1;
            }
        });
        requestAnimationFrame(syncAllTabIndicators);
        if (sub === 'logs') startMonitoring();
        else stopMonitoring();
    }

    function updateTextCount() {
        const input = $('test-text');
        if ($('test-text-count') && input) $('test-text-count').textContent = input.value.length.toLocaleString();
    }

    function credentialValue(field) {
        const element = document.querySelector(`[data-credential-field="${field}"]`);
        return element ? element.value.trim() : '';
    }

    function hasCredential(field) {
        return Boolean(credentialValue(field) || state.configuredFields[field]);
    }

    function isPotentialCustomVoiceId(provider, value) {
        const voice = String(value || '').trim();
        if (!CUSTOM_VOICE_ID_PATTERN.test(voice)) return false;
        if (provider === 'fishaudio') {
            return voice !== 'custom' && voice !== 'fish-default';
        }
        if (provider !== 'doubao') return false;
        if (voice.includes('Neural') && voice.includes('-')) return false;
        if (/^\d+$/.test(voice)) return false;
        if (voice === 'default_zh' || voice === 'default_eh' || voice.startsWith('mimo_')) return false;
        if (voice === 'custom' || voice.startsWith('fish-')) return false;
        return true;
    }

    function supportsCustomVoices(provider = state.provider) {
        return CUSTOM_VOICE_PROVIDERS.has(provider);
    }

    function customVoiceItems(provider = state.provider) {
        return (state.voiceCatalogs[provider] || []).filter(voice => voice.custom === true);
    }

    function hasUsableCurrentVoice() {
        const voice = currentVoiceId();
        if (!voice) return false;
        return (state.voiceCatalogs[state.provider] || []).some(item => item.id === voice);
    }

    function readConfigField(element) {
        if (!element) return undefined;
        if (element.type === 'checkbox') return Boolean(element.checked);
        if (element.type === 'number') {
            const raw = element.value.trim();
            return raw ? Number(raw) : '';
        }
        return element.value.trim();
    }

    function collectConfigFields() {
        const values = {};
        $$('[data-config-field]').forEach(element => {
            const key = element.dataset.configField;
            if (key) values[key] = readConfigField(element);
        });
        return values;
    }

    function hydrateFallbackSettings(source) {
        const enabled = initialBoolean(
            initialConfigValue(source, 'fallback_enabled', FALLBACK_DEFAULTS.enabled),
            FALLBACK_DEFAULTS.enabled,
        );
        const voice = String(initialConfigValue(
            source, 'fallback_voice', FALLBACK_DEFAULTS.voice,
        ) || FALLBACK_DEFAULTS.voice).trim() || FALLBACK_DEFAULTS.voice;
        const retryCount = initialInteger(
            initialConfigValue(source, 'fallback_retry_count', FALLBACK_DEFAULTS.retryCount),
            FALLBACK_DEFAULTS.retryCount,
            FALLBACK_LIMITS.retryCount,
        );
        const retryDelayMs = initialInteger(
            initialConfigValue(source, 'fallback_retry_delay_ms', FALLBACK_DEFAULTS.retryDelayMs),
            FALLBACK_DEFAULTS.retryDelayMs,
            FALLBACK_LIMITS.retryDelayMs,
        );
        const enabledInput = $('fallback-enabled');
        const voiceInput = $('fallback-voice');
        const retryInput = $('fallback-retry-count');
        const delayInput = $('fallback-retry-delay-ms');
        if (enabledInput) enabledInput.checked = enabled;
        if (voiceInput) voiceInput.value = voice;
        if (retryInput) retryInput.value = String(retryCount);
        if (delayInput) delayInput.value = String(retryDelayMs);
        renderFallbackVoiceOptions([], voice);
        updateFallbackControls();
    }

    function renderFallbackVoiceOptions(voices = [], selectedVoice = '') {
        const select = $('fallback-voice');
        if (!select) return;
        const list = Array.isArray(voices)
            ? voices.filter(voice => voice && typeof voice.id === 'string' && voice.id)
            : [];
        const selected = String(selectedVoice || select.value || '').trim();
        const hasSelected = list.some(voice => voice.id === selected);
        const options = hasSelected || !selected
            ? list
            : [{id: selected, name: `当前音色 · ${selected}`}, ...list];
        select.replaceChildren();
        options.forEach(voice => {
            const option = document.createElement('option');
            option.value = voice.id;
            option.textContent = voice.name || voice.id;
            option.selected = voice.id === selected;
            select.appendChild(option);
        });
        if (!options.length) {
            const option = document.createElement('option');
            option.value = selected;
            option.textContent = selected || '没有可用的 Edge 音色';
            option.selected = true;
            option.disabled = !selected;
            select.appendChild(option);
        }
        if (selected) select.value = selected;
        select.dataset.catalogReady = String(Boolean(list.length));
        updateFallbackControls();
    }

    function updateFallbackControls() {
        const enabledInput = $('fallback-enabled');
        const fields = $('fallback-fields');
        if (!enabledInput || !fields) return;
        const enabled = Boolean(enabledInput.checked);
        fields.classList.toggle('is-disabled', !enabled);
        const voiceInput = $('fallback-voice');
        if (voiceInput) voiceInput.disabled = state.fallbackVoiceLoading || !enabled;
        const retryInput = $('fallback-retry-count');
        const delayInput = $('fallback-retry-delay-ms');
        if (retryInput) retryInput.disabled = !enabled;
        if (delayInput) delayInput.disabled = !enabled;
    }

    async function loadFallbackVoices() {
        const select = $('fallback-voice');
        if (!select) return;
        const selected = select.value.trim() || FALLBACK_DEFAULTS.voice;
        state.fallbackVoiceLoading = true;
        updateFallbackControls();
        if (state.baselineCore) updateDirtyState();
        try {
            let voices = state.voiceCatalogs.edge;
            if (!Array.isArray(voices)) {
                const response = await expectOk(await api('/api/voices?provider=edge'));
                voices = await response.json();
                if (!Array.isArray(voices)) throw new Error('音色列表格式不正确');
                state.voiceCatalogs.edge = voices;
            }
            renderFallbackVoiceOptions(voices, selected);
            setFeedback('fallback-feedback');
        } catch (error) {
            // Keep the saved ID visible when the catalog endpoint is temporarily unavailable.
            renderFallbackVoiceOptions([], selected);
            setFeedback('fallback-feedback', `Edge 备用音色加载失败：${error.message}`, 'warning');
        } finally {
            state.fallbackVoiceLoading = false;
            updateFallbackControls();
            if (state.baselineCore) updateDirtyState();
        }
    }

    function validateFallbackDraft() {
        if (!$('fallback-enabled')) return '';
        if ($('fallback-enabled').checked) {
            const voice = $('fallback-voice').value.trim();
            const voices = state.voiceCatalogs.edge || [];
            if (!voice) return '请选择 Edge 备用音色。';
            if (voices.length && !voices.some(item => item.id === voice)) {
                return '备用音色必须属于 Edge 音色列表。';
            }
            if (!voices.length && voice !== FALLBACK_DEFAULTS.voice) {
                return '请等待 Edge 备用音色列表加载完成。';
            }
        }
        const fields = [
            ['fallback-retry-count', '重试次数', FALLBACK_LIMITS.retryCount],
            ['fallback-retry-delay-ms', '重试间隔', FALLBACK_LIMITS.retryDelayMs],
        ];
        for (const [id, label, limits] of fields) {
            const input = $(id);
            const value = input && input.value.trim() ? Number(input.value) : NaN;
            if (!Number.isInteger(value) || value < limits.min || value > limits.max) {
                return `${label}需为 ${limits.min} 至 ${limits.max} 之间的整数。`;
            }
        }
        return '';
    }

    function validateProviderDraft(provider = state.provider) {
        const fallbackError = validateFallbackDraft();
        if (fallbackError) return fallbackError;
        const voices = state.voiceCatalogs[provider];
        if (state.voiceLoading || !voices) return '请等待当前服务商的音色列表加载完成。';
        if (!voices.some(voice => voice.id === state.selectedVoices[provider])) {
            return '当前音色无效，请重新选择。';
        }
        const missing = (PROVIDER_CREDENTIALS[provider] || []).filter(field => !hasCredential(field));
        if (!missing.length) return '';
        if (provider === 'tencent') return '请填写 SecretId 与 SecretKey，或先保留一套已保存的完整凭证。';
        return `请先填写 ${providerMeta(provider).name} 的 API Key。`;
    }

    function coreSnapshot() {
        const data = {provider: state.provider};
        Object.entries(VOICE_FIELDS).forEach(([provider, field]) => {
            data[field] = state.selectedVoices[provider] || '';
        });
        return JSON.stringify(Object.assign(data, collectConfigFields()));
    }

    function normalizeBaselineVoice(provider, voice) {
        if (!state.baselineCore) return;
        try {
            const baseline = JSON.parse(state.baselineCore);
            baseline[VOICE_FIELDS[provider]] = voice;
            state.baselineCore = JSON.stringify(baseline);
        } catch (_) {}
    }

    function collectDraft() {
        const payload = Object.assign({provider: state.provider}, collectConfigFields());
        const voiceProviders = new Set(state.voiceTouched);
        voiceProviders.add(state.provider);
        voiceProviders.forEach(provider => {
            const field = VOICE_FIELDS[provider];
            const voice = state.selectedVoices[provider];
            if (field && voice) payload[field] = voice;
        });
        $$('[data-credential-field]').forEach(element => {
            const value = element.value.trim();
            if (value) payload[element.dataset.credentialField] = value;
        });
        return payload;
    }

    function updateDirtyState() {
        const providerChanged = state.provider !== state.savedProvider;
        const credentialChanged = $$('[data-credential-field]').some(element => Boolean(element.value.trim()));
        state.dirty = state.needsProviderRepair
            || providerChanged
            || credentialChanged
            || coreSnapshot() !== state.baselineCore;
        const chip = $('save-chip');
        chip.classList.toggle('is-saved', !state.dirty);
        chip.classList.toggle('is-dirty', state.dirty);
        $('save-chip-text').textContent = state.dirty ? '有未保存更改' : '已保存';
        $('save-state-title').textContent = state.dirty ? '当前设置尚未保存' : '所有更改均已保存';
        $('save-state-description').textContent = state.dirty
            ? '可以先测试当前草稿，确认后再保存到服务器。'
            : '切换服务商和音色不会立即影响服务器。';
        const waitingForPrimaryCatalog = state.voiceLoading;
        const waitingForFallbackCatalog = state.fallbackVoiceLoading && Boolean($('fallback-enabled') && $('fallback-enabled').checked);
        const waitingForVoiceCatalog = waitingForPrimaryCatalog || waitingForFallbackCatalog;
        const saveButton = $('save-button');
        if (!saveButton.hasAttribute('aria-busy')) saveButton.disabled = !state.dirty || waitingForVoiceCatalog;
        saveButton.classList.toggle('is-dirty-pulse', state.dirty && !waitingForVoiceCatalog);
        const floatingSaveBtn = $('floating-save-btn');
        if (floatingSaveBtn) floatingSaveBtn.disabled = !state.dirty || waitingForVoiceCatalog;
        const floatingSaveStatus = $('floating-save-status');
        if (floatingSaveStatus) {
            floatingSaveStatus.textContent = state.dirty
                ? `草稿已修改 · ${providerMeta().short}`
                : '所有更改均已保存';
        }
        syncFloatingSaveBar();
        const testButton = $('test-config-button');
        if (!testButton.hasAttribute('aria-busy')) testButton.disabled = waitingForVoiceCatalog;
        const floatingTestBtn = $('floating-test-btn');
        if (floatingTestBtn) floatingTestBtn.disabled = waitingForVoiceCatalog;
        const testTtsButton = $('test-tts-button');
        if (!testTtsButton.hasAttribute('aria-busy')) {
            testTtsButton.disabled = waitingForVoiceCatalog || !hasUsableCurrentVoice();
        }
    }

    function updateCredentialPlaceholders() {
        $$('[data-credential-field]').forEach(element => {
            const field = element.dataset.credentialField;
            const configured = Boolean(state.configuredFields[field]);
            const labels = {
                doubao_api_key: '火山引擎 API Key',
                tencent_secret_id: 'SecretId',
                tencent_secret_key: 'SecretKey',
                xiaomi_api_key: '小米 MiMo API Key',
                fishaudio_api_key: 'Fish Audio API Key',
            };
            element.placeholder = configured ? '已配置，留空不修改' : `输入 ${labels[field] || '凭证'}`;
        });
    }

    function renderProviderStatuses() {
        let readyCount = 0;
        Object.keys(PROVIDERS).forEach(provider => {
            const element = $(`${provider}-status`);
            const status = state.providerStatus[provider] || {};
            const ready = provider === 'edge' || Boolean(status.ready);
            if (ready) readyCount += 1;
            if (element) {
                element.textContent = provider === 'edge' ? '免费' : (ready ? '已配置' : '待配置');
                element.classList.toggle('is-ready', ready);
                element.classList.toggle('is-missing', !ready);
            }
            const heroPill = document.querySelector(`[data-hero-provider="${provider}"]`);
            if (heroPill) {
                heroPill.classList.toggle('is-ready', ready);
                heroPill.classList.toggle('is-missing', !ready);
                heroPill.classList.toggle('is-active', provider === state.provider);
            }
        });
        if ($('sidebar-voice-badge')) {
            $('sidebar-voice-badge').textContent = String(readyCount);
        }
        renderCurrentCredentialState();
    }

    function renderCurrentCredentialState() {
        const badge = $('credential-badge');
        const status = state.providerStatus[state.provider] || {};
        const ready = state.provider === 'edge' || Boolean(status.ready);
        badge.classList.toggle('is-ready', ready);
        badge.classList.toggle('is-missing', !ready);
        badge.textContent = state.provider === 'edge' ? '无需配置' : (ready ? '凭证已配置' : '尚未配置');
        $('credential-title').textContent = providerMeta().credentialTitle;
    }

    function currentVoiceId() {
        return state.selectedVoices[state.provider] || '';
    }

    function voiceName(voiceId, provider = state.provider) {
        const list = state.voiceCatalogs[provider] || [];
        const voice = list.find(item => item.id === voiceId);
        return voice ? voice.name : voiceId || '未选择';
    }

    function updateSetupSummary() {
        const voiceId = currentVoiceId();
        const voice = voiceName(voiceId);
        if ($('setup-summary')) $('setup-summary').textContent = `${providerMeta().short} · ${voice || '未选择音色'}`;
        if ($('hero-voice-id')) $('hero-voice-id').textContent = voiceId || '未选择音色 ID';
        $$('[data-hero-provider]').forEach(pill => {
            pill.classList.toggle('is-active', pill.dataset.heroProvider === state.provider);
        });
        $('active-provider-name').textContent = `${providerMeta().name} · ${voice || '未选择音色'}`;
        if ($('play-voice-label')) $('play-voice-label').textContent = `当前草稿 · ${providerMeta().name}`;
        $('stats-provider-title').textContent = `${providerMeta().name} 使用量`;
    }

    function stashCustomVoiceForm(provider = state.provider) {
        if (!supportsCustomVoices(provider)) return;
        const draft = {
            name: $('custom-voice-name').value,
            id: $('custom-voice-id').value,
            editingId: $('custom-voice-save').dataset.editingId || '',
        };
        if (draft.name || draft.id || draft.editingId) state.customVoiceDrafts[provider] = draft;
        else delete state.customVoiceDrafts[provider];
    }

    function setCustomVoiceSaveLabel(text) {
        const button = $('custom-voice-save');
        if (!button) return;
        const label = button.querySelector('.btn-label');
        if (label) label.textContent = text;
        else button.textContent = text;
    }

    function restoreCustomVoiceForm(provider = state.provider) {
        const draft = state.customVoiceDrafts[provider] || {};
        $('custom-voice-name').value = draft.name || '';
        $('custom-voice-id').value = draft.id || '';
        if (draft.editingId) $('custom-voice-save').dataset.editingId = draft.editingId;
        else delete $('custom-voice-save').dataset.editingId;
        setCustomVoiceSaveLabel(draft.editingId ? '保存音色' : '添加音色');
        setFeedback('custom-voice-feedback');
    }

    function resetCustomVoiceForm(provider = state.provider) {
        delete state.customVoiceDrafts[provider];
        if (provider !== state.provider) return;
        $('custom-voice-name').value = '';
        $('custom-voice-id').value = '';
        $('custom-voice-id').disabled = false;
        delete $('custom-voice-save').dataset.editingId;
        setCustomVoiceSaveLabel('添加音色');
        setFeedback('custom-voice-feedback');
    }

    function editCustomVoice(voice) {
        $('custom-voice-name').value = voice.name;
        $('custom-voice-id').value = voice.id;
        $('custom-voice-id').disabled = true;
        $('custom-voice-save').dataset.editingId = voice.id;
        setCustomVoiceSaveLabel('保存音色');
        setFeedback('custom-voice-feedback', `正在编辑“${voice.name}”。`, 'info');
        renderCustomVoiceManager();
        $('custom-voice-name').focus();
    }

    function renderCustomVoiceManager() {
        const manager = $('custom-voice-manager');
        const supported = supportsCustomVoices();
        manager.hidden = !supported;
        if (!supported) return;

        const fish = state.provider === 'fishaudio';
        const voices = customVoiceItems();
        $('custom-voice-provider').textContent = providerMeta().name;
        const idLabel = $('custom-voice-id-label');
        if (idLabel) idLabel.textContent = fish ? 'Reference ID' : '音色 ID';
        $('custom-voice-id').placeholder = fish ? '输入 Fish Audio Reference ID' : '输入 voice_type';
        const hint = $('custom-voice-hint');
        if (hint) {
            hint.textContent = fish
                ? '填写 Fish Audio 声音模型的 Reference ID；保存后可从上方列表一键选择。'
                : '填写火山引擎的 voice_type；保存后可从上方列表一键选择。';
        }
        $('custom-voice-summary').textContent = `${voices.length} 个已保存音色`;
        $('custom-voice-empty').hidden = Boolean(voices.length);
        $('custom-voice-cancel').hidden = !$('custom-voice-save').dataset.editingId;
        $('custom-voice-name').disabled = state.customVoiceBusy;
        $('custom-voice-id').disabled = state.customVoiceBusy
            || Boolean($('custom-voice-save').dataset.editingId);

        const list = $('custom-voice-list');
        list.replaceChildren();
        voices.forEach(voice => {
            const row = document.createElement('div');
            row.className = `custom-voice-item${voice.id === currentVoiceId() ? ' is-current' : ''}`;

            const copy = document.createElement('span');
            copy.className = 'custom-voice-copy';
            const name = document.createElement('strong');
            name.textContent = voice.name;
            const id = document.createElement('small');
            id.textContent = voice.id;
            copy.append(name, id);

            const actions = document.createElement('span');
            actions.className = 'custom-voice-actions';
            const use = document.createElement('button');
            use.className = 'btn btn-secondary btn-sm custom-voice-use';
            use.type = 'button';
            use.textContent = voice.id === currentVoiceId() ? '已选择' : '选择';
            use.disabled = state.customVoiceBusy || voice.id === currentVoiceId();
            use.addEventListener('click', () => selectVoice(voice.id));

            const edit = document.createElement('button');
            edit.className = 'custom-voice-icon';
            edit.type = 'button';
            edit.appendChild(createSvgIcon('icon-edit'));
            edit.title = `编辑 ${voice.name}`;
            edit.setAttribute('aria-label', `编辑音色 ${voice.name}`);
            edit.disabled = state.customVoiceBusy;
            edit.addEventListener('click', () => editCustomVoice(voice));

            const remove = document.createElement('button');
            remove.className = 'custom-voice-icon is-danger';
            remove.type = 'button';
            remove.appendChild(createSvgIcon('icon-trash'));
            remove.title = `删除 ${voice.name}`;
            remove.setAttribute('aria-label', `删除音色 ${voice.name}`);
            remove.disabled = state.customVoiceBusy;
            remove.addEventListener('click', () => deleteCustomVoice(voice));

            actions.append(use, edit, remove);
            row.append(copy, actions);
            list.appendChild(row);
        });
    }

    function selectVoice(voiceId) {
        const list = state.voiceCatalogs[state.provider] || [];
        if (!list.some(voice => voice.id === voiceId)) return;
        clearSynthesisOutputs();
        state.selectedVoices[state.provider] = voiceId;
        state.voiceTouched.add(state.provider);
        $('voice-search').value = '';
        renderVoiceSelect();
        updateDirtyState();
        updateLegadoConfig();
    }

    function invalidateLegadoConfig(message = '正在生成配置…') {
        state.configRequestId += 1;
        state.legadoConfigText = '';
        if ($('legado-config')) $('legado-config').textContent = message;
        if ($('subscribe-url-display')) $('subscribe-url-display').value = message;
        $('copy-config-button').disabled = true;
        $('copy-subscribe-button').disabled = true;
        setFeedback('integration-feedback');
    }

    function acceptVoiceCatalog(provider, voices) {
        if (!Array.isArray(voices)) throw new Error('音色列表格式不正确');
        const requestId = (state.voiceRequestIds[provider] || 0) + 1;
        state.voiceRequestIds[provider] = requestId;
        state.voiceCatalogs[provider] = voices;
        if (provider === state.provider) {
            state.voiceLoading = false;
            renderVoiceSelect();
        }
    }

    async function saveCustomVoice() {
        if (state.customVoiceBusy) return;
        const provider = state.provider;
        const name = $('custom-voice-name').value.trim();
        const voiceId = $('custom-voice-id').value.trim();
        const editingId = $('custom-voice-save').dataset.editingId || '';
        if (!supportsCustomVoices(provider)) return;
        if (!name) {
            setFeedback('custom-voice-feedback', '请填写音色名称。', 'warning');
            return;
        }
        if (name.length > 80) {
            setFeedback('custom-voice-feedback', '音色名称不能超过 80 个字符。', 'warning');
            return;
        }
        if (!isPotentialCustomVoiceId(provider, voiceId)) {
            setFeedback('custom-voice-feedback', '音色 ID 仅支持字母、数字、点、下划线、连字符和冒号，最长 128 个字符。', 'warning');
            return;
        }
        const catalogMatch = (state.voiceCatalogs[provider] || []).find(voice => voice.id === voiceId);
        if (catalogMatch && catalogMatch.custom !== true) {
            setFeedback('custom-voice-feedback', '该 ID 已存在于内置音色中，请直接选择。', 'warning');
            return;
        }

        const button = $('custom-voice-save');
        state.customVoiceBusy = true;
        setButtonBusy(button, true, '保存中');
        renderCustomVoiceManager();
        setFeedback('custom-voice-feedback', '正在保存到服务器…', 'info');
        try {
            const data = await jsonRequest('/api/custom-voices', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider, id: voiceId, name}),
            });
            if (!editingId) {
                state.selectedVoices[provider] = voiceId;
                state.voiceTouched.add(provider);
            }
            acceptVoiceCatalog(provider, data.voices);
            resetCustomVoiceForm(provider);
            updateDirtyState();
            if (provider === state.provider) {
                updateSetupSummary();
                updateLegadoConfig();
                setFeedback(
                    'custom-voice-feedback',
                    editingId
                        ? '音色名称已保存到服务器。'
                        : '音色已保存到服务器并选中；点击“保存更改”后应用当前选择。',
                    'success',
                );
            }
            notify('自定义音色已保存', 'success');
        } catch (error) {
            if (provider === state.provider) {
                setFeedback('custom-voice-feedback', `音色保存失败：${error.message}`, 'error');
            }
            notify(`${providerMeta(provider).name}音色保存失败`, 'error');
        } finally {
            state.customVoiceBusy = false;
            setButtonBusy(button, false);
            delete button.dataset.idleText;
            setCustomVoiceSaveLabel(button.dataset.editingId ? '保存音色' : '添加音色');
            renderCustomVoiceManager();
        }
    }

    async function deleteCustomVoice(voice) {
        if (state.customVoiceBusy) return;
        const provider = state.provider;
        if (voice.id === currentVoiceId()) {
            setFeedback('custom-voice-feedback', '请先选择并保存另一个音色，再删除当前音色。', 'warning');
            return;
        }
        const confirmed = await confirmAction({
            title: '删除自定义音色',
            message: `确定删除自定义音色“${voice.name}”吗？删除后将从服务器列表中移除。`,
            confirmText: '确认删除',
            tone: 'danger',
        });
        if (!confirmed) return;
        state.customVoiceBusy = true;
        renderCustomVoiceManager();
        setFeedback('custom-voice-feedback', '正在从服务器删除音色…', 'info');
        try {
            const data = await jsonRequest('/api/custom-voices', {
                method: 'DELETE',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({provider, id: voice.id}),
            });
            acceptVoiceCatalog(provider, data.voices);
            if (provider === state.provider) {
                setFeedback('custom-voice-feedback', '自定义音色已删除。', 'success');
            }
            notify('自定义音色已删除', 'success');
        } catch (error) {
            if (provider === state.provider) {
                setFeedback('custom-voice-feedback', `删除失败：${error.message}`, 'error');
            }
            notify(`${providerMeta(provider).name}音色删除失败`, 'error');
        } finally {
            state.customVoiceBusy = false;
            renderCustomVoiceManager();
        }
    }

    function applyProvider(provider, markDirty = true) {
        if (!hasOwn(PROVIDERS, provider)) return;
        const previousProvider = state.provider;
        const changed = state.provider !== provider;
        if (changed) {
            stashCustomVoiceForm(previousProvider);
            clearSynthesisOutputs();
        }
        state.provider = provider;
        if (changed) restoreCustomVoiceForm(provider);
        $$(PROVIDER_BUTTON_SELECTOR).forEach(button => {
            const active = button.dataset.provider === provider;
            button.classList.toggle('is-active', active);
            button.classList.toggle('active', active);
            button.setAttribute('aria-checked', String(active));
            button.tabIndex = active ? 0 : -1;
        });
        $$('[data-settings-provider]').forEach(panel => {
            const active = panel.dataset.settingsProvider === provider;
            panel.classList.toggle('is-active', active);
            panel.hidden = !active;
            panel.style.display = active ? '' : 'none';
            panel.setAttribute('aria-hidden', String(!active));
            panel.inert = !active;
        });
        renderCurrentCredentialState();
        renderCustomVoiceManager();
        updateSetupSummary();
        renderStats();
        loadVoices(provider);
        if (markDirty) {
            setFeedback('settings-feedback');
            updateDirtyState();
        }
    }

    function renderVoiceSelect() {
        const list = state.voiceCatalogs[state.provider] || [];
        const query = $('voice-search').value.trim().toLowerCase();
        const selectedId = currentVoiceId();
        const matches = list.filter(voice => !query || voice.name.toLowerCase().includes(query) || voice.id.toLowerCase().includes(query));
        const selectedVoice = list.find(voice => voice.id === selectedId);
        const options = selectedVoice && !matches.some(voice => voice.id === selectedId)
            ? [selectedVoice, ...matches]
            : matches;
        const select = $('voice-select');
        select.replaceChildren();
        options.forEach((voice, index) => {
            const option = document.createElement('option');
            option.value = voice.id;
            option.textContent = voice === selectedVoice && index === 0 && query && !matches.includes(voice)
                ? `${voice.name}（当前）`
                : voice.name;
            option.selected = voice.id === selectedId;
            select.appendChild(option);
        });
        if (!options.length) {
            const option = document.createElement('option');
            option.textContent = '没有匹配的音色';
            option.disabled = true;
            option.selected = true;
            select.appendChild(option);
        }
        select.disabled = !list.length;
        const usableVoice = hasUsableCurrentVoice();
        if (!$('preview-button').hasAttribute('aria-busy')) $('preview-button').disabled = !usableVoice;
        $('all-voices-button').disabled = !list.length;
        $('voice-count').textContent = query ? `${matches.length} / ${list.length} 个音色` : `${list.length} 个音色`;
        fillCompareSelects(list);
        renderCustomVoiceManager();
        updateSetupSummary();
    }

    function fillCompareSelects(list) {
        const first = $('compare-a');
        const second = $('compare-b');
        const previousA = first.value;
        const previousB = second.value;
        [first, second].forEach(select => select.replaceChildren());
        list.forEach(voice => {
            const optionA = document.createElement('option');
            optionA.value = voice.id;
            optionA.textContent = voice.name;
            first.appendChild(optionA);
            const optionB = optionA.cloneNode(true);
            second.appendChild(optionB);
        });
        if (list.some(voice => voice.id === previousA)) first.value = previousA;
        else first.value = currentVoiceId() || (list[0] && list[0].id) || '';
        if (list.some(voice => voice.id === previousB)) second.value = previousB;
        else second.value = (list[1] && list[1].id) || (list[0] && list[0].id) || '';
        first.disabled = !list.length;
        second.disabled = !list.length;
        if (!$('compare-button').hasAttribute('aria-busy')) $('compare-button').disabled = !list.length;
    }

    async function loadVoices(provider) {
        const requestId = (state.voiceRequestIds[provider] || 0) + 1;
        state.voiceRequestIds[provider] = requestId;
        const cached = state.voiceCatalogs[provider];
        if (cached) {
            if (provider === state.provider) {
                state.voiceLoading = false;
                renderVoiceSelect();
                updateLegadoConfig();
                updateDirtyState();
            }
            return;
        }
        if (provider === state.provider) {
            state.voiceLoading = true;
            updateDirtyState();
            $('voice-select').disabled = true;
            $('preview-button').disabled = true;
            $('all-voices-button').disabled = true;
            $('compare-a').replaceChildren(new Option('正在加载音色…'));
            $('compare-b').replaceChildren(new Option('正在加载音色…'));
            $('compare-a').disabled = true;
            $('compare-b').disabled = true;
            $('compare-button').disabled = true;
            state.configRequestId += 1;
            state.legadoConfigText = '';
            $('legado-config').textContent = '正在加载当前服务商的音色…';
            $('copy-config-button').disabled = true;
            $('copy-subscribe-button').disabled = true;
            $('voice-count').textContent = '正在加载音色';
            setFeedback('voice-feedback');
        }
        try {
            const response = await expectOk(await api(`/api/voices?provider=${encodeURIComponent(provider)}`));
            const voices = await response.json();
            if (!Array.isArray(voices)) throw new Error('音色列表格式不正确');
            if (requestId !== state.voiceRequestIds[provider] || provider !== state.provider) return;
            state.voiceLoading = false;
            state.voiceCatalogs[provider] = voices;
            const selectedVoice = state.selectedVoices[provider];
            const selectedPreset = voices.find(voice => voice.id === selectedVoice);
            if (!selectedPreset && voices.length) {
                state.selectedVoices[provider] = voices[0].id;
                if (provider === state.savedProvider) state.voiceTouched.add(provider);
                else normalizeBaselineVoice(provider, voices[0].id);
            }
            renderVoiceSelect();
            updateDirtyState();
            updateLegadoConfig();
        } catch (error) {
            if (requestId !== state.voiceRequestIds[provider] || provider !== state.provider) return;
            state.voiceLoading = false;
            $('voice-select').replaceChildren(new Option('音色加载失败'));
            $('voice-select').disabled = true;
            $('voice-count').textContent = '加载失败';
            $('preview-button').disabled = true;
            $('all-voices-button').disabled = true;
            $('compare-a').replaceChildren();
            $('compare-b').replaceChildren();
            $('compare-a').disabled = true;
            $('compare-b').disabled = true;
            $('compare-button').disabled = true;
            setFeedback('voice-feedback', `音色加载失败：${error.message}`, 'error');
            updateDirtyState();
        }
    }

    function getLegadoClientKey() {
        return ($('legado-api-key')?.value || '').trim();
    }

    function legadoSubscriptionAllowed() {
        // Never put client credentials into a URL, including an encoded
        // subscription. Authenticated engines are exported as JSON only.
        return !INITIAL.apiKeysRequired && !getLegadoClientKey();
    }

    function withLegadoClientKey(config, clientKey) {
        if (!clientKey) return config;
        if (/[\x00-\x1f\x7f]/.test(clientKey)) {
            throw new Error('客户端 API Key 不能包含控制字符');
        }
        const delimiter = typeof config.url === 'string' ? config.url.indexOf(',') : -1;
        if (delimiter < 0) throw new Error('引擎配置格式无效');
        const options = JSON.parse(config.url.slice(delimiter + 1));
        options.headers = {...options.headers, Authorization: `Bearer ${clientKey}`};
        return {...config, url: `${config.url.slice(0, delimiter)},${JSON.stringify(options)}`};
    }

    async function updateLegadoConfig() {
        const voice = currentVoiceId();
        if (!voice || !hasUsableCurrentVoice()) {
            invalidateLegadoConfig(voice ? '当前音色不可用，请重新选择。' : '请先选择音色。');
            return;
        }
        const clientKey = getLegadoClientKey();
        if (INITIAL.apiKeysRequired && !clientKey) {
            invalidateLegadoConfig('本服务要求 API Key，请填写客户端密钥后复制 JSON 配置。');
            setFeedback('integration-feedback', '认证已启用：请输入管理员分配的客户端 API Key。订阅链接不可用于此认证模式。', 'warning');
            return;
        }
        const requestId = ++state.configRequestId;
        const code = $('legado-config');
        state.legadoConfigText = '';
        code.textContent = '正在生成配置…';
        $('copy-config-button').disabled = true;
        $('copy-subscribe-button').disabled = true;
        setFeedback('integration-feedback');
        try {
            const origin = window.location && window.location.origin && window.location.origin !== 'null'
                ? window.location.origin : '';
            const serverParam = origin ? `&server=${encodeURIComponent(origin)}` : '';
            const template = await jsonRequest(`/api/legado/config?voice=${encodeURIComponent(voice)}&provider=${encodeURIComponent(state.provider)}${serverParam}`);
            if (requestId !== state.configRequestId || voice !== currentVoiceId()) return;
            // Add only the explicitly entered client key, entirely locally.
            // getToken() is intentionally not used: it holds the admin secret.
            const data = withLegadoClientKey(template, clientKey);
            state.legadoConfigText = JSON.stringify(data);
            code.textContent = JSON.stringify(data, null, 2);
            const subDisplay = $('subscribe-url-display');
            if (subDisplay) {
                const subUrl = `${origin}/api/legado/subscribe?voice=${encodeURIComponent(voice)}&provider=${encodeURIComponent(state.provider)}&auto=true`;
                subDisplay.value = legadoSubscriptionAllowed()
                    ? subUrl : '认证配置仅支持复制 JSON；密钥不会写入订阅链接。';
            }
            if (!$('copy-config-button').hasAttribute('aria-busy')) $('copy-config-button').disabled = false;
            if (!$('copy-subscribe-button').hasAttribute('aria-busy')) $('copy-subscribe-button').disabled = !legadoSubscriptionAllowed();
            if (clientKey) {
                setFeedback('integration-feedback', 'JSON 已包含客户端密钥，请妥善保管并勿公开分享。为避免泄漏，订阅链接已禁用。', 'warning');
            }
        } catch (error) {
            if (requestId !== state.configRequestId) return;
            state.legadoConfigText = '';
            code.textContent = '配置生成失败';
            setFeedback('integration-feedback', `配置生成失败：${error.message}`, 'error');
        }
    }

    async function copyConfig() {
        const button = $('copy-config-button');
        setButtonBusy(button, true, '复制中');
        try {
            await copyText(state.legadoConfigText);
            if (button && button.classList) {
                button.classList.add('is-copied');
                if (typeof window.setTimeout === 'function') {
                    window.setTimeout(() => button.classList.remove('is-copied'), 1600);
                }
            }
            notify('JSON 配置已复制', 'success');
            setFeedback('integration-feedback', 'JSON 配置已复制到剪贴板。', 'success');
        } catch (error) {
            setFeedback('integration-feedback', `复制失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
            button.disabled = !state.legadoConfigText;
        }
    }

    async function copySubscribeUrl() {
        if (!legadoSubscriptionAllowed()) {
            setFeedback('integration-feedback', '认证配置不能通过订阅链接传递，请复制 JSON 配置。', 'warning');
            return;
        }
        const voice = currentVoiceId();
        if (!voice) return;
        const selectedProvider = state.provider;
        const button = $('copy-subscribe-button');
        setButtonBusy(button, true, '生成中');
        try {
            const provider = encodeURIComponent(selectedProvider);
            const origin = window.location && window.location.origin && window.location.origin !== 'null'
                ? window.location.origin : '';
            const serverParam = origin ? `&server=${encodeURIComponent(origin)}` : '';
            const data = await jsonRequest(`/api/legado/subscribe?voice=${encodeURIComponent(voice)}&provider=${provider}${serverParam}`);
            const url = data.url || `${origin}/api/legado/subscribe?voice=${encodeURIComponent(voice)}&provider=${provider}&auto=true`;
            if (voice !== currentVoiceId() || selectedProvider !== state.provider
                    || !legadoSubscriptionAllowed()) {
                throw new Error('音色或认证设置已更改，请重新复制');
            }
            if ($('subscribe-url-display')) $('subscribe-url-display').value = url;
            await copyText(url);
            if (button && button.classList) {
                button.classList.add('is-copied');
                if (typeof window.setTimeout === 'function') {
                    window.setTimeout(() => button.classList.remove('is-copied'), 1600);
                }
            }
            notify('订阅链接已复制', 'success');
            setFeedback('integration-feedback', '订阅链接已复制，可直接导入开源阅读。', 'success');
        } catch (error) {
            setFeedback('integration-feedback', `复制订阅链接失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
            button.disabled = !hasUsableCurrentVoice() || !legadoSubscriptionAllowed();
        }
    }

    async function synthesize(text, voice, rate = '0%') {
        setSynthesizing(1);
        try {
            const response = await expectOk(await api('/speech/stream', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text, voice, provider: state.provider, rate}),
            }));
            return {response, blob: await response.blob()};
        } finally {
            setSynthesizing(-1);
        }
    }

    function getTestRate() {
        const input = $('test-rate');
        const value = input ? Number(input.value) : 0;
        if (!Number.isFinite(value) || value === 0) return '0%';
        return `${value > 0 ? '+' : ''}${value}%`;
    }

    function updateRateDisplay() {
        const input = $('test-rate');
        const readout = $('test-rate-value');
        const resetBtn = $('test-rate-reset');
        if (!input) return;
        const min = Number(input.min || -50);
        const max = Number(input.max || 100);
        const val = Number(input.value || 0);
        const pct = ((val - min) / (max - min)) * 100;
        input.style.setProperty('--range-pct', `${pct.toFixed(1)}%`);
        const formatted = val > 0 ? `+${val}%` : `${val}%`;
        if (readout) readout.textContent = formatted;
        if (resetBtn) resetBtn.classList.toggle('is-modified', val !== 0);
    }

    function synthesisFeedback(result, successMessage) {
        const fallback = result.response.headers.get('X-TTS-Fallback') === 'true';
        const actualProvider = result.response.headers.get('X-TTS-Provider');
        if (fallback) {
            return {type: 'warning', message: `已生成音频，但原服务不可用，实际由 ${providerMeta(actualProvider).name} 完成。`};
        }
        return {type: 'success', message: successMessage};
    }

    async function previewVoice() {
        const voice = currentVoiceId();
        if (!voice) return;
        const requestId = ++state.previewRequestId;
        const button = $('preview-button');
        resetAudioElement($('preview-player'));
        setButtonBusy(button, true, '生成中');
        setFeedback('voice-feedback', '正在生成试听音频…', 'info');
        try {
            const result = await synthesize('你好，我是您的朗读助手，很高兴认识您。', voice);
            if (requestId !== state.previewRequestId) return;
            const player = $('preview-player');
            setAudioBlob(player, result.blob, `快速试听 · ${voiceName(voice)}`);
            const feedback = synthesisFeedback(result, '试听音频已生成。');
            setFeedback('voice-feedback', feedback.message, feedback.type);
            await playAudio(player, 'voice-feedback');
        } catch (error) {
            if (requestId !== state.previewRequestId) return;
            setFeedback('voice-feedback', `试听失败：${error.message}`, 'error');
        } finally {
            if (requestId === state.previewRequestId) {
                setButtonBusy(button, false);
                button.disabled = !hasUsableCurrentVoice();
            }
        }
    }

    async function testTTS() {
        const text = $('test-text').value.trim();
        const voice = currentVoiceId();
        if (!text) {
            setFeedback('test-feedback', '请输入测试文本。', 'warning');
            $('test-text').focus();
            return;
        }
        if (!voice) {
            setFeedback('test-feedback', '请先在快速配置中选择音色。', 'warning');
            return;
        }
        const requestId = ++state.testRequestId;
        const button = $('test-tts-button');
        const rate = getTestRate();
        resetAudioElement($('test-audio-player'));
        setButtonBusy(button, true, '正在合成…');
        setFeedback('test-feedback', '正在生成音频…', 'info');
        try {
            const result = await synthesize(text, voice, rate);
            if (requestId !== state.testRequestId) return;
            const player = $('test-audio-player');
            const rateTag = rate !== '0%' ? ` (${rate})` : '';
            setAudioBlob(player, result.blob, `${providerMeta().short} · ${voiceName(voice)}${rateTag}`);
            const feedback = synthesisFeedback(result, '测试音频已生成。');
            setFeedback('test-feedback', feedback.message, feedback.type);
            await playAudio(player, 'test-feedback');
        } catch (error) {
            if (requestId !== state.testRequestId) return;
            setFeedback('test-feedback', `生成失败：${error.message}`, 'error');
        } finally {
            if (requestId === state.testRequestId) {
                setButtonBusy(button, false);
                button.disabled = state.voiceLoading || !hasUsableCurrentVoice();
            }
        }
    }

    async function compareVoices() {
        const text = $('compare-text').value.trim();
        const voiceA = $('compare-a').value;
        const voiceB = $('compare-b').value;
        if (!text || !voiceA || !voiceB) {
            setFeedback('compare-feedback', '请输入文本并选择两个音色。', 'warning');
            return;
        }
        const requestId = ++state.compareRequestId;
        const button = $('compare-button');
        resetAudioElement($('audio-a'));
        resetAudioElement($('audio-b'));
        setButtonBusy(button, true, '并行合成中…');
        setFeedback('compare-feedback', '正在同时生成两个音频…', 'info');
        try {
            const [resultA, resultB] = await Promise.all([synthesize(text, voiceA), synthesize(text, voiceB)]);
            if (requestId !== state.compareRequestId) return;
            setAudioBlob($('audio-a'), resultA.blob, `音色 A · ${voiceName(voiceA)}`);
            setAudioBlob($('audio-b'), resultB.blob, `音色 B · ${voiceName(voiceB)}`);
            const fallback = [resultA, resultB].some(result => result.response.headers.get('X-TTS-Fallback') === 'true');
            setFeedback('compare-feedback', fallback
                ? '对比音频已生成，但至少一个音色触发了 Edge 回退，请结合连接测试判断配置。'
                : '两个对比音频均已生成，可点击播放器切换聆听。', fallback ? 'warning' : 'success');
        } catch (error) {
            if (requestId !== state.compareRequestId) return;
            setFeedback('compare-feedback', `对比失败：${error.message}`, 'error');
        } finally {
            if (requestId === state.compareRequestId) {
                setButtonBusy(button, false);
                button.disabled = !(state.voiceCatalogs[state.provider] || []).length;
            }
        }
    }

    async function saveConfig() {
        const validation = validateProviderDraft();
        if (validation) {
            setFeedback('settings-feedback', validation, 'warning');
            notify('当前服务商配置不完整', 'warning');
            return;
        }
        const button = $('save-button');
        const draft = collectDraft();
        const submittedCore = coreSnapshot();
        setButtonBusy(button, true, '保存中');
        setFeedback('settings-feedback', '正在保存当前设置…', 'info');
        try {
            const data = await jsonRequest('/api/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(draft),
            });
            state.savedProvider = draft.provider;
            state.needsProviderRepair = false;
            if (data.providerStatus || data.provider_status) state.providerStatus = data.providerStatus || data.provider_status;
            if (data.configuredFields || data.configured_fields) state.configuredFields = data.configuredFields || data.configured_fields;
            $$('[data-credential-field]').forEach(element => {
                const field = element.dataset.credentialField;
                if (draft[field] && element.value.trim() === draft[field]) element.value = '';
            });
            state.baselineCore = submittedCore;
            renderProviderStatuses();
            updateCredentialPlaceholders();
            updateDirtyState();
            const warnings = Array.isArray(data.warnings) ? data.warnings : [];
            if (warnings.length) {
                setFeedback('settings-feedback', `设置已保存，但有提示：${warnings.join('；')}`, 'warning');
                notify('设置已保存，但存在兼容性提示', 'warning');
            } else {
                setFeedback('settings-feedback', '设置已保存并立即生效。', 'success');
                notify('设置已保存', 'success');
            }
        } catch (error) {
            setFeedback('settings-feedback', `保存失败：${error.message}`, 'error');
            notify('设置未保存', 'error');
        } finally {
            setButtonBusy(button, false);
            button.disabled = !state.dirty;
        }
    }

    async function testConfig() {
        const validation = validateProviderDraft();
        if (validation) {
            setFeedback('settings-feedback', validation, 'warning');
            return;
        }
        const button = $('test-config-button');
        setButtonBusy(button, true, '测试中');
        setFeedback('settings-feedback', '正在使用当前表单草稿连接服务商，此操作不会保存配置…', 'info');
        try {
            const data = await jsonRequest('/api/config/test', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(collectDraft()),
            });
            if (!data.ok) throw new Error(data.error || '服务商未通过测试');
            const size = Number(data.audio_size || 0).toLocaleString();
            setFeedback('settings-feedback', `连接成功：${providerMeta(data.provider).name} 已返回 ${size} 字节测试音频。当前草稿尚未自动保存。`, 'success');
            notify('当前配置连接成功', 'success');
        } catch (error) {
            setFeedback('settings-feedback', `连接测试失败：${error.message}`, 'error');
            notify('连接测试失败', 'error');
        } finally {
            setButtonBusy(button, false);
        }
    }

    function localDateKey() {
        const date = new Date();
        const pad = value => String(value).padStart(2, '0');
        return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    }

    function renderStats() {
        const data = state.stats[state.provider] || {};
        $('total-chars').textContent = Number(data.total_chars || 0).toLocaleString();
        $('total-requests').textContent = Number(data.total_requests || 0).toLocaleString();
        const today = (data.history || []).find(item => item.date === localDateKey()) || {};
        $('today-chars').textContent = Number(today.chars || 0).toLocaleString();
        $('today-requests').textContent = Number(today.requests || 0).toLocaleString();
        $('monitor-summary').textContent = state.stats[state.provider]
            ? `今日 ${Number(today.requests || 0).toLocaleString()} 次请求`
            : '尚未加载';
        updateSetupSummary();
    }

    async function loadStats() {
        const response = await expectOk(await api('/api/stats'));
        const data = await response.json();
        if (!data || typeof data !== 'object') throw new Error('统计数据格式不正确');
        state.stats = data;
        renderStats();
    }

    function parseMetrics(text) {
        const metrics = {totalChars: 0, totalRequests: 0, cacheHitRate: 0, p95: 0};
        for (const line of text.split('\n')) {
            const [name, rawValue] = line.trim().split(/\s+/, 2);
            const value = Number(rawValue);
            if (!Number.isFinite(value)) continue;
            if (name === 'tts_chars_total') metrics.totalChars = value;
            if (name === 'tts_requests_total') metrics.totalRequests = value;
            if (name === 'tts_cache_hit_ratio') metrics.cacheHitRate = value;
            if (name === 'tts_response_time_ms_p95') metrics.p95 = value;
        }
        return metrics;
    }

    function setServiceStatus(kind, text) {
        const element = $('service-status');
        if (element) {
            element.className = `status-badge is-${kind}`;
            $('service-status-text').textContent = text;
        }
    }

    function renderHealth(health, metrics = null) {
        const cache = health.cache || {};
        $('cache-count').textContent = Number(cache.count || cache.size || 0).toLocaleString();
        $('cache-memory').textContent = `${(Number(cache.bytes || 0) / (1024 * 1024)).toFixed(1)} MB`;
        const ffmpegEl = $('ffmpeg-status');
        ffmpegEl.textContent = health.ffmpeg_available ? '可用' : '不可用';
        ffmpegEl.style.color = health.ffmpeg_available ? 'var(--green)' : 'var(--yellow)';
        ffmpegEl.style.background = health.ffmpeg_available ? 'var(--green-soft)' : 'var(--yellow-soft)';
        ffmpegEl.style.borderColor = 'transparent';
        const adminEl = $('admin-status');
        adminEl.textContent = health.admin_protected ? '已启用' : '未启用';
        adminEl.style.color = health.admin_protected ? 'var(--green)' : 'var(--text-secondary)';
        adminEl.style.background = health.admin_protected ? 'var(--green-soft)' : 'var(--bg-subtle)';
        adminEl.style.borderColor = health.admin_protected ? 'transparent' : 'var(--border)';
        if (metrics) {
            const hitPct = Number(metrics.cacheHitRate || 0) * 100;
            $('total-chars-all').textContent = Number(metrics.totalChars || 0).toLocaleString();
            $('total-requests-all').textContent = Number(metrics.totalRequests || 0).toLocaleString();
            $('cache-hit-rate').textContent = `${hitPct.toFixed(1)}%`;
            if ($('cache-hit-bar')) {
                $('cache-hit-bar').style.width = `${Math.min(100, Math.max(0, hitPct)).toFixed(1)}%`;
            }
            $('response-time-p95').textContent = `${Math.round(Number(metrics.p95 || 0))} ms`;
        }
        setServiceStatus('online', `服务正常 · v${health.version || INITIAL.version || ''}`);
    }

    async function loadHealthOnly() {
        try {
            await loadSystemStatus();
        } catch (error) {
            try {
                const health = await jsonRequest('/health');
                renderHealth(health);
            } catch (_) {
                setServiceStatus('error', '服务状态异常');
                if (isSectionOpen('monitoring')) setFeedback('monitor-feedback', `健康状态加载失败：${error.message}`, 'error');
            }
        }
    }

    async function loadSystemStatus() {
        const [healthResponse, metricsResponse] = await Promise.all([
            expectOk(await api('/health')),
            expectOk(await api('/metrics')),
        ]);
        const health = await healthResponse.json();
        const metrics = parseMetrics(await metricsResponse.text());
        renderHealth(health, metrics);
        $('monitor-updated-at').textContent = `更新于 ${new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'})}`;
    }

    async function refreshMonitoring(showSuccess = false) {
        const button = $('refresh-monitor-button');
        setButtonBusy(button, true, '刷新中');
        const results = await Promise.allSettled([loadStats(), loadSystemStatus()]);
        const errors = results.filter(result => result.status === 'rejected').map(result => result.reason.message);
        if (errors.length) {
            setFeedback('monitor-feedback', `部分监控数据加载失败：${errors.join('；')}`, 'error');
        } else {
            setFeedback('monitor-feedback', showSuccess ? '监控数据已刷新。' : '', 'success');
        }
        setButtonBusy(button, false);
    }

    function setLiveState(kind, text) {
        const element = $('live-state');
        element.className = `live-state is-${kind}`;
        element.querySelector('span:last-child').textContent = text;
    }

    function appendActivity(data) {
        const feed = $('activity-feed');
        const empty = $('activity-empty');
        if (empty) empty.hidden = true;
        const line = document.createElement('div');
        const failed = data.outcome === 'failed' || data.outcome === 'stream_failed'
            || data.status < 200 || data.status >= 300;
        line.className = `activity-line${failed ? ' is-error' : ''}`;
        line.dataset.requestId = data.request_id || '';
        const time = document.createElement('span');
        time.textContent = (data.ts || '').split('T')[1]?.split('.')[0] || '刚刚';
        const provider = document.createElement('span');
        provider.textContent = data.provider || '未知';
        const voice = document.createElement('span');
        voice.textContent = data.voice || '—';
        const chars = document.createElement('span');
        chars.textContent = `${Number(data.chars || 0)} 字`;
        const status = document.createElement('span');
        status.className = failed ? '' : 'activity-ok';
        status.textContent = data.outcome === 'stream_failed'
            ? `流中断 · ${Math.round(Number(data.ms || 0))}ms`
            : `${data.status || '—'} · ${Math.round(Number(data.ms || 0))}ms`;
        line.append(time, provider, voice, chars, status);
        feed.appendChild(line);
        while (feed.querySelectorAll('.activity-line').length > 50) {
            feed.querySelector('.activity-line').remove();
        }
        feed.scrollTop = feed.scrollHeight;
    }

    function ensureActivityStream() {
        if (state.activitySource) return;
        const token = getToken();
        if (INITIAL.adminProtected && !token) {
            setLiveState('error', '需要管理令牌');
            return;
        }
        // Native EventSource cannot send an Authorization header. Use fetch
        // instead so the long-lived admin credential never enters a URL/log.
        const controller = new AbortController();
        const source = {
            timer: null,
            close() {
                controller.abort();
                if (this.timer) window.clearTimeout(this.timer);
            },
        };
        state.activitySource = source;

        function receive(dataText) {
            try {
                const data = JSON.parse(dataText);
                if (data.type === 'connected') setLiveState('live', '实时连接');
                if (data.type === 'tts_request') appendActivity(data);
                if (data.type === 'tts_request_update') {
                    const existing = Array.from($$('#activity-feed .activity-line'))
                        .find(line => line.dataset.requestId === data.request_id);
                    if (existing) existing.remove();
                    appendActivity(data);
                }
            } catch (_) {}
        }

        async function connect() {
            let reader = null;
            let retry = true;
            try {
                setLiveState('connecting', '正在连接');
                const response = await api('/api/events', {
                    signal: controller.signal,
                    headers: {'Accept': 'text/event-stream'},
                    cache: 'no-store',
                });
                if (!response.ok) {
                    retry = ![401, 403].includes(response.status);
                    throw new Error(await errorText(response));
                }
                if (!response.body) throw new Error('浏览器不支持流式响应');
                reader = response.body.getReader();
                const decoder = new TextDecoder();
                let pending = '';
                let dataLines = [];
                setLiveState('live', '实时连接');
                while (!controller.signal.aborted) {
                    const {value, done} = await reader.read();
                    if (done) break;
                    pending += decoder.decode(value, {stream: true});
                    let newline;
                    while ((newline = pending.indexOf('\n')) !== -1) {
                        const line = pending.slice(0, newline).replace(/\r$/, '');
                        pending = pending.slice(newline + 1);
                        if (!line) {
                            if (dataLines.length) receive(dataLines.join('\n'));
                            dataLines = [];
                        } else if (line.startsWith('data:')) {
                            dataLines.push(line.slice(5).replace(/^ /, ''));
                        }
                    }
                }
            } catch (error) {
                if (!controller.signal.aborted) {
                    setLiveState(retry ? 'connecting' : 'error',
                        retry ? '连接中断，重试中' : error.message);
                }
            } finally {
                if (reader) {
                    try { await reader.cancel(); } catch (_) {}
                    reader.releaseLock();
                }
                if (!controller.signal.aborted && state.activitySource === source && retry) {
                    setLiveState('connecting', '连接中断，重试中');
                    source.timer = window.setTimeout(connect, 3000);
                }
            }
        }
        connect();
    }

    function stopActivityStream() {
        if (state.activitySource) state.activitySource.close();
        state.activitySource = null;
        setLiveState('paused', '已暂停');
    }

    function startMonitoring() {
        refreshMonitoring(false);
        ensureActivityStream();
        if (state.monitorTimer) window.clearInterval(state.monitorTimer);
        state.monitorTimer = window.setInterval(() => refreshMonitoring(false), 30000);
    }

    function stopMonitoring() {
        if (state.monitorTimer) window.clearInterval(state.monitorTimer);
        state.monitorTimer = null;
        stopActivityStream();
    }

    async function exportConfig() {
        const button = $('export-button');
        setButtonBusy(button, true, '导出中');
        setFeedback('maintenance-feedback');
        try {
            const response = await expectOk(await api('/api/config/export'));
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `tts-config-${localDateKey()}.json`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.setTimeout(() => URL.revokeObjectURL(url), 1000);
            setFeedback('maintenance-feedback', '配置已导出。文件包含完整凭证，请安全保存。', 'success');
            notify('配置已导出', 'success');
        } catch (error) {
            setFeedback('maintenance-feedback', `导出失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
        }
    }

    async function importConfig(event) {
        const input = event.currentTarget;
        const file = input.files && input.files[0];
        if (!file) return;
        setFeedback('maintenance-feedback');
        try {
            const text = await file.text();
            const data = JSON.parse(text);
            if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('文件内容必须是 JSON 对象');
            const warning = state.dirty
                ? '导入会覆盖服务器配置，并丢弃页面中尚未保存的草稿，确定继续吗？'
                : '导入会用文件内容覆盖当前配置，确定继续吗？';
            const confirmed = await confirmAction({
                title: '导入配置文件',
                message: warning,
                confirmText: '确认覆盖导入',
                tone: 'warning',
            });
            if (!confirmed) return;
            const result = await jsonRequest('/api/config/import', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data),
            });
            const ignored = Array.isArray(result.ignored_keys) && result.ignored_keys.length
                ? `；已忽略未知字段：${result.ignored_keys.join('、')}` : '';
            state.needsProviderRepair = false;
            $$('[data-credential-field]').forEach(element => { element.value = ''; });
            state.baselineCore = coreSnapshot();
            updateDirtyState();
            setFeedback('maintenance-feedback', `配置已导入${ignored}，页面即将刷新。`, ignored ? 'warning' : 'success');
            notify('配置已导入', 'success');
            window.setTimeout(() => window.location.reload(), 900);
        } catch (error) {
            setFeedback('maintenance-feedback', `导入失败：${error.message}`, 'error');
        } finally {
            input.value = '';
        }
    }

    async function clearAudioCache() {
        const confirmed = await confirmAction({
            title: '清空音频缓存',
            message: '确定清空当前内存中的所有合成音频缓存吗？后续相同文本将重新合成。',
            confirmText: '确认清空',
            tone: 'warning',
        });
        if (!confirmed) return;
        const button = $('clear-cache-button');
        setButtonBusy(button, true, '清空中');
        setFeedback('maintenance-feedback');
        try {
            await jsonRequest('/api/cache/clear', {method: 'DELETE'});
            await loadHealthOnly();
            setFeedback('maintenance-feedback', '音频缓存已清空。', 'success');
            notify('音频缓存已清空', 'success');
        } catch (error) {
            setFeedback('maintenance-feedback', `清空缓存失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
        }
    }

    async function resetStats() {
        const confirmed = await confirmAction({
            title: '重置全部统计',
            message: '确定重置全部服务商的累计字符与请求统计吗？此操作无法撤销。',
            confirmText: '确认重置',
            tone: 'danger',
        });
        if (!confirmed) return;
        const button = $('reset-stats-button');
        setButtonBusy(button, true, '重置中');
        setFeedback('maintenance-feedback');
        try {
            await jsonRequest('/api/stats', {method: 'DELETE'});
            await loadStats();
            setFeedback('maintenance-feedback', '累计统计已重置。', 'success');
            notify('统计已重置', 'success');
        } catch (error) {
            setFeedback('maintenance-feedback', `重置失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
        }
    }

    function setLibraryPreviewButtonPlaying(button, playing) {
        if (!button) return;
        button.classList.toggle('is-previewing', playing);
        const card = typeof button.closest === 'function' ? button.closest('.voice-card') : null;
        if (card) card.classList.toggle('is-previewing', playing);
        const use = button.querySelector('use');
        if (use) use.setAttribute('href', playing ? '#icon-pause' : '#icon-play');
        const label = button.querySelector('.btn-label');
        if (label) label.textContent = playing ? '停止' : '试听';
    }

    function clearModalAudio() {
        if (state.modalPreviewButton) {
            setLibraryPreviewButtonPlaying(state.modalPreviewButton, false);
            state.modalPreviewButton = null;
        }
        state.modalPreviewVoiceId = '';
        const audio = state.modalAudio;
        const url = state.modalAudioUrl;
        state.modalAudio = null;
        state.modalAudioUrl = '';
        if (audio) {
            audio.pause();
            audio.removeAttribute('src');
        }
        if (url) URL.revokeObjectURL(url);
    }

    function stopModalAudio() {
        state.modalPreviewId += 1;
        clearModalAudio();
    }

    function closeVoiceDialog() {
        const dialog = $('voice-dialog');
        if (dialog.open && typeof dialog.close === 'function') dialog.close();
        else dialog.removeAttribute('open');
    }

    function chooseVoiceFromLibrary(voiceId) {
        selectVoice(voiceId);
        closeVoiceDialog();
        notify(`已选择音色：${voiceName(voiceId)}`, 'success');
    }

    async function previewLibraryVoice(voice, button) {
        if (state.modalAudio && state.modalPreviewVoiceId === voice.id) {
            stopModalAudio();
            return;
        }
        const requestId = ++state.modalPreviewId;
        clearModalAudio();
        setButtonBusy(button, true, '加载');
        try {
            const result = await synthesize('你好，我是这个音色的朗读效果。', voice.id);
            if (requestId !== state.modalPreviewId || !$('voice-dialog').open) return;
            const url = URL.createObjectURL(result.blob);
            const audio = new Audio(url);
            state.modalAudio = audio;
            state.modalAudioUrl = url;
            state.modalPreviewButton = button;
            state.modalPreviewVoiceId = voice.id;
            audio.addEventListener('ended', () => {
                if (state.modalAudio === audio) clearModalAudio();
            });
            audio.addEventListener('pause', () => {
                if (state.modalAudio === audio && !audio.ended) clearModalAudio();
            });
            pauseOtherAudio(audio);
            setButtonBusy(button, false);
            setLibraryPreviewButtonPlaying(button, true);
            await audio.play();
            const fallback = result.response.headers.get('X-TTS-Fallback') === 'true';
            if (fallback) notify('试听已回退到 Edge，当前服务商可能未配置成功', 'warning', 5000);
        } catch (error) {
            if (state.modalPreviewButton === button) clearModalAudio();
            if (requestId !== state.modalPreviewId || !$('voice-dialog').open) return;
            notify(`试听失败：${error.message}`, 'error');
        } finally {
            if (button.hasAttribute('aria-busy')) setButtonBusy(button, false);
        }
    }

    function renderVoiceLibrary() {
        const list = state.voiceCatalogs[state.provider] || [];
        const query = $('dialog-voice-search').value.trim().toLowerCase();
        const filtered = list.filter(voice => !query || voice.name.toLowerCase().includes(query) || voice.id.toLowerCase().includes(query));
        const library = $('voice-library');
        library.replaceChildren();
        filtered.forEach(voice => {
            const card = document.createElement('article');
            card.className = `voice-card${voice.id === currentVoiceId() ? ' is-current' : ''}${voice.custom === true ? ' is-custom' : ''}`;
            const copy = document.createElement('span');
            copy.className = 'voice-card-copy';
            const name = document.createElement('strong');
            name.textContent = voice.name;
            if (voice.custom === true) {
                const badge = document.createElement('span');
                badge.className = 'voice-card-badge';
                badge.textContent = '自定义';
                name.appendChild(badge);
            }
            const id = document.createElement('small');
            id.textContent = voice.id;
            copy.append(name, id);

            const actions = document.createElement('span');
            actions.className = 'voice-card-actions';
            const preview = document.createElement('button');
            preview.className = 'btn btn-secondary btn-sm voice-card-preview';
            preview.type = 'button';
            preview.appendChild(createSvgIcon('icon-play'));
            const previewLabel = document.createElement('span');
            previewLabel.className = 'btn-label';
            previewLabel.textContent = '试听';
            preview.appendChild(previewLabel);
            preview.addEventListener('click', () => previewLibraryVoice(voice, preview));
            const use = document.createElement('button');
            use.className = 'btn btn-primary btn-sm voice-card-use';
            use.type = 'button';
            use.textContent = voice.id === currentVoiceId() ? '已选择' : '使用';
            use.disabled = voice.id === currentVoiceId();
            use.addEventListener('click', () => chooseVoiceFromLibrary(voice.id));
            if (state.modalAudio && state.modalPreviewVoiceId === voice.id) {
                state.modalPreviewButton = preview;
                setLibraryPreviewButtonPlaying(preview, true);
            }
            actions.append(preview, use);
            card.append(copy, actions);
            library.appendChild(card);
        });
        $('dialog-voice-count').textContent = `${filtered.length} / ${list.length} 个音色`;
        $('dialog-empty').hidden = Boolean(filtered.length);
    }

    function openVoiceDialog() {
        const list = state.voiceCatalogs[state.provider] || [];
        if (!list.length) return;
        $('voice-dialog-title').textContent = `${providerMeta().name} 音色库`;
        $('dialog-voice-search').value = '';
        renderVoiceLibrary();
        const dialog = $('voice-dialog');
        if (typeof dialog.showModal === 'function') dialog.showModal();
        else dialog.setAttribute('open', '');
        window.setTimeout(() => $('dialog-voice-search').focus(), 30);
    }

    function handleProviderKeydown(event) {
        const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'];
        if (!keys.includes(event.key)) return;
        event.preventDefault();
        const buttons = $$(PROVIDER_BUTTON_SELECTOR);
        const current = buttons.indexOf(event.currentTarget);
        if (!buttons.length || current < 0) return;
        let next = current;
        if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = buttons.length - 1;
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = (current - 1 + buttons.length) % buttons.length;
        else next = (current + 1) % buttons.length;
        const target = buttons[next];
        applyProvider(target.dataset.provider);
        target.focus();
    }

    function bindEvents() {
        $('theme-button').addEventListener('click', toggleTheme);
        $$('[data-theme-option]').forEach(option => {
            option.addEventListener('click', () => {
                applyThemeMode(option.dataset.themeOption);
                setThemeMenuOpen(false);
            });
        });
        document.addEventListener('mousedown', event => {
            const menu = $('theme-menu');
            if (menu && !menu.contains(event.target)) setThemeMenuOpen(false);
        });
        if (typeof window.matchMedia === 'function') {
            const mq = window.matchMedia('(prefers-color-scheme: dark)');
            const onSchemeChange = () => {
                if (getThemeMode() === 'auto') applyThemeMode('auto', false);
            };
            if (typeof mq.addEventListener === 'function') mq.addEventListener('change', onSchemeChange);
            else if (typeof mq.addListener === 'function') mq.addListener(onSchemeChange);
        }
        if ($('header-refresh-button')) {
            $('header-refresh-button').addEventListener('click', async () => {
                const btn = $('header-refresh-button');
                btn.classList.remove('is-spinning');
                void btn.offsetWidth;
                btn.classList.add('is-spinning');
                window.setTimeout(() => btn.classList.remove('is-spinning'), 560);
                try {
                    await Promise.allSettled([loadSystemStatus(), loadStats()]);
                    notify('运行状态已刷新', 'success', 2200);
                } catch (_) {}
            });
        }
        if ($('floating-save-btn')) $('floating-save-btn').addEventListener('click', saveConfig);
        if ($('floating-test-btn')) $('floating-test-btn').addEventListener('click', testConfig);
        $$('[data-hero-action="studio"]').forEach(btn => {
            btn.addEventListener('click', () => {
                setConsolePage('main');
                switchSubTab('test');
                const el = $('section-studio');
                if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
                window.setTimeout(() => { if ($('test-text')) $('test-text').focus(); }, 180);
            });
        });
        $$('[data-hero-action="integration"]').forEach(btn => {
            btn.addEventListener('click', () => {
                setConsolePage('main');
                const el = $('section-integration');
                if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
            });
        });
        if ($('token-button')) $('token-button').addEventListener('click', setAdminToken);
        if ($('maintenance-token-button')) $('maintenance-token-button').addEventListener('click', setAdminToken);
        $$(PROVIDER_BUTTON_SELECTOR).forEach(button => {
            button.addEventListener('click', () => applyProvider(button.dataset.provider));
            button.addEventListener('keydown', handleProviderKeydown);
        });
        $$('[data-subtab-target]').forEach(btn => {
            btn.addEventListener('click', () => switchSubTab(btn.dataset.subtabTarget));
            btn.addEventListener('keydown', event => {
                if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
                event.preventDefault();
                const tabs = $$('[data-subtab-target]');
                const current = tabs.indexOf(event.currentTarget);
                let next = event.key === 'ArrowLeft' ? current - 1 : current + 1;
                if (event.key === 'Home') next = 0;
                if (event.key === 'End') next = tabs.length - 1;
                const target = tabs[(next + tabs.length) % tabs.length];
                switchSubTab(target.dataset.subtabTarget);
                target.focus();
            });
        });
        if ($('toggle-json-btn')) {
            $('toggle-json-btn').addEventListener('click', () => {
                const pre = $('legado-config');
                if (pre) {
                    const willOpen = !isCollapseOpen(pre);
                    setCollapseOpen(pre, willOpen);
                    $('toggle-json-btn').textContent = willOpen ? '收起 JSON' : '查看 JSON';
                    $('toggle-json-btn').setAttribute('aria-expanded', String(willOpen));
                }
            });
        }
        if ($('clear-logs-btn')) {
            $('clear-logs-btn').addEventListener('click', () => {
                const feed = $('activity-feed');
                if (feed) {
                    feed.replaceChildren();
                    const p = document.createElement('p');
                    p.id = 'activity-empty';
                    p.className = 'empty-state';
                    p.textContent = '日志已清空，等待新的合成请求…';
                    feed.appendChild(p);
                }
            });
        }
        $('settings-form').addEventListener('submit', event => {
            event.preventDefault();
            if (state.dirty) saveConfig();
        });

        $('voice-search').addEventListener('input', renderVoiceSelect);
        $('test-text').addEventListener('input', updateTextCount);
        $('test-text').addEventListener('keydown', event => {
            if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey)) return;
            event.preventDefault();
            if (!$('test-tts-button').disabled) testTTS();
        });
        $('voice-select').addEventListener('change', event => {
            if (!event.currentTarget.value) return;
            selectVoice(event.currentTarget.value);
        });
        $('custom-voice-save').addEventListener('click', saveCustomVoice);
        $('custom-voice-cancel').addEventListener('click', () => {
            resetCustomVoiceForm();
            renderCustomVoiceManager();
            $('custom-voice-name').focus();
        });
        ['custom-voice-name', 'custom-voice-id'].forEach(id => {
            $(id).addEventListener('keydown', event => {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                saveCustomVoice();
            });
        });
        $('preview-button').addEventListener('click', previewVoice);
        $('all-voices-button').addEventListener('click', openVoiceDialog);
        $('copy-config-button').addEventListener('click', copyConfig);
        $('copy-subscribe-button').addEventListener('click', copySubscribeUrl);
        $('legado-api-key').addEventListener('input', updateLegadoConfig);
        $('save-button').addEventListener('click', saveConfig);
        $('test-config-button').addEventListener('click', testConfig);
        $('test-tts-button').addEventListener('click', testTTS);
        $('compare-button').addEventListener('click', compareVoices);
        $('refresh-monitor-button').addEventListener('click', () => refreshMonitoring(true));
        $('export-button').addEventListener('click', exportConfig);
        $('import-button').addEventListener('click', () => $('import-file').click());
        $('import-file').addEventListener('change', importConfig);
        if ($('clear-cache-button')) $('clear-cache-button').addEventListener('click', clearAudioCache);
        $('reset-stats-button').addEventListener('click', resetStats);

        // Password visibility eye toggles
        $$('[data-toggle-secret]').forEach(toggle => {
            toggle.addEventListener('click', () => {
                const target = $(toggle.dataset.toggleSecret);
                if (!target) return;
                const showing = target.type === 'text';
                target.type = showing ? 'password' : 'text';
                toggle.setAttribute('aria-pressed', String(!showing));
                const use = toggle.querySelector('use');
                if (use) use.setAttribute('href', showing ? '#icon-eye' : '#icon-eye-off');
                target.focus();
            });
        });

        // Studio speech rate slider & sample text presets
        if ($('test-rate')) {
            $('test-rate').addEventListener('input', updateRateDisplay);
            updateRateDisplay();
        }
        if ($('test-rate-reset')) {
            $('test-rate-reset').addEventListener('click', () => {
                if ($('test-rate')) {
                    $('test-rate').value = '0';
                    updateRateDisplay();
                }
            });
        }
        $$('[data-sample-text]').forEach(chip => {
            chip.addEventListener('click', () => {
                const area = $('test-text');
                if (!area) return;
                area.value = chip.dataset.sampleText || '';
                updateTextCount();
                area.focus();
            });
        });

        // Fallback advanced retry parameters toggle
        if ($('fallback-advanced-btn') && $('fallback-advanced-panel')) {
            $('fallback-advanced-btn').addEventListener('click', () => {
                const panel = $('fallback-advanced-panel');
                const willOpen = !isCollapseOpen(panel);
                setCollapseOpen(panel, willOpen);
                $('fallback-advanced-btn').setAttribute('aria-expanded', String(willOpen));
            });
        }

        // Custom Admin Token modal
        const tokenDialog = $('token-dialog');
        if (tokenDialog) {
            const closeTokenDialog = () => closeDialogElement(tokenDialog);
            if ($('token-dialog-close')) $('token-dialog-close').addEventListener('click', closeTokenDialog);
            if ($('token-dialog-cancel')) $('token-dialog-cancel').addEventListener('click', closeTokenDialog);
            if ($('token-dialog-clear')) {
                $('token-dialog-clear').addEventListener('click', () => {
                    closeTokenDialog();
                    saveAdminTokenValue('');
                });
            }
            if ($('token-dialog-form')) {
                $('token-dialog-form').addEventListener('submit', event => {
                    event.preventDefault();
                    const value = $('token-dialog-input') ? $('token-dialog-input').value : '';
                    closeTokenDialog();
                    saveAdminTokenValue(value);
                });
            }
            tokenDialog.addEventListener('click', event => {
                const rect = tokenDialog.getBoundingClientRect();
                const outside = event.clientX < rect.left || event.clientX > rect.right
                    || event.clientY < rect.top || event.clientY > rect.bottom;
                if (outside) closeTokenDialog();
            });
        }

        // Keep sliding tab indicators aligned on resize
        window.addEventListener('resize', syncAllTabIndicators);
        if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(syncAllTabIndicators);
            if ($('console-nav-tabs')) ro.observe($('console-nav-tabs'));
            if ($('studio-sub-tabs')) ro.observe($('studio-sub-tabs'));
        }

        const authBadge = $('legado-auth-badge');
        if (authBadge) authBadge.classList.toggle('is-required', Boolean(INITIAL.apiKeysRequired));

        initStudioPlayers();

        $$('[data-credential-field], [data-config-field]').forEach(element => {
            const handleConfigInput = () => {
                if (element.id === 'fallback-enabled') updateFallbackControls();
                setFeedback('settings-feedback');
                updateDirtyState();
            };
            element.addEventListener('input', handleConfigInput);
            if (element.type === 'checkbox' || element.tagName === 'SELECT') {
                element.addEventListener('change', handleConfigInput);
            }
        });

        $('voice-dialog-close').addEventListener('click', closeVoiceDialog);
        $('dialog-voice-search').addEventListener('input', renderVoiceLibrary);
        $('voice-dialog').addEventListener('close', stopModalAudio);
        $('voice-dialog').addEventListener('click', event => {
            const dialog = event.currentTarget;
            const rect = dialog.getBoundingClientRect();
            const outside = event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
            if (outside) closeVoiceDialog();
        });

        window.addEventListener('beforeunload', event => {
            if (!state.dirty) return;
            event.preventDefault();
            event.returnValue = '';
        });
        window.addEventListener('pagehide', () => {
            stopMonitoring();
            stopModalAudio();
            $$('audio[data-blob-url]').forEach(audio => URL.revokeObjectURL(audio.dataset.blobUrl));
        });
        window.addEventListener('pageshow', event => {
            if (event.persisted && state.initialized && isMonitoringVisible()) startMonitoring();
        });
    }

    async function initialize() {
        updateThemeButton();
        updateTextCount();
        initCollapsibles();
        initSidebarController();
        initBackToTop();
        initConsoleNavigation();
        let loadedConfig = null;
        let configLoadError = '';
        const shouldLoadConfig = (INITIAL.adminProtected && getToken())
            || (!INITIAL.adminProtected && !hasInitialFallbackSettings());
        if (shouldLoadConfig) {
            try {
                loadedConfig = await jsonRequest('/api/config');
                if (loadedConfig.provider_status) state.providerStatus = loadedConfig.provider_status;
                if (loadedConfig.configured_fields) state.configuredFields = loadedConfig.configured_fields;
            } catch (error) {
                configLoadError = error.message;
            }
        }
        const hydratedConfig = Object.assign({}, INITIAL, loadedConfig || {});
        hydrateFallbackSettings(hydratedConfig);
        if (loadedConfig) {
            if (hasOwn(PROVIDERS, loadedConfig.provider)) {
                state.provider = loadedConfig.provider;
                state.savedProvider = loadedConfig.provider;
            }
            Object.entries(VOICE_FIELDS).forEach(([provider, field]) => {
                if (typeof loadedConfig[field] === 'string' && loadedConfig[field]) {
                    state.selectedVoices[provider] = loadedConfig[field];
                }
            });
        }
        bindEvents();
        state.baselineCore = coreSnapshot();
        renderProviderStatuses();
        updateCredentialPlaceholders();
        applyProvider(state.provider, false);
        updateDirtyState();
        loadFallbackVoices();
        loadHealthOnly();

        if (invalidInitialProvider) {
            setFeedback('settings-feedback', `已保存的服务商 “${INITIAL.provider}” 无法识别，界面已安全回退到 Edge；保存后可修复配置。`, 'warning');
            notify('检测到无效的旧服务商配置', 'warning', 6000);
        } else if (INITIAL.adminProtected && !getToken()) {
            setFeedback('settings-feedback', '本服务已启用管理保护；保存、测试和维护操作需要先设置管理令牌。', 'warning');
        } else if (configLoadError) {
            setFeedback('settings-feedback', `无法读取服务器设置：${configLoadError}`, 'warning');
        }
        state.initialized = true;
        requestAnimationFrame(syncAllTabIndicators);
        if (isMonitoringVisible()) startMonitoring();
    }

    initialize().catch(error => {
        setFeedback('settings-feedback', `界面初始化失败：${error.message}`, 'error');
        notify('界面初始化失败', 'error', 6000);
    });
})();
