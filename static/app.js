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

    const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
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
            fishaudio: 'fish-animated',
        }, INITIAL.voices || {}),
        providerStatus: INITIAL.providerStatus || {},
        configuredFields: INITIAL.configuredFields || {},
        voiceCatalogs: {},
        voiceTouched: new Set(),
        voiceLoading: false,
        stats: {},
        baselineCore: '',
        dirty: false,
        needsProviderRepair: invalidInitialProvider,
        voiceRequestId: 0,
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
        previewRequestId: 0,
        testRequestId: 0,
        compareRequestId: 0,
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

    function notify(message, type = 'info', duration = 3500) {
        const region = $('toast-region');
        if (!region) return;
        const toast = document.createElement('div');
        const icons = {success: '✓', warning: '!', error: '×', info: 'i'};
        toast.className = `toast is-${type}`;
        toast.setAttribute('role', type === 'error' ? 'alert' : 'status');

        const icon = document.createElement('span');
        icon.className = 'toast-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = icons[type] || icons.info;

        const text = document.createElement('span');
        text.className = 'toast-message';
        text.textContent = String(message || '');

        const close = document.createElement('button');
        close.className = 'toast-close';
        close.type = 'button';
        close.setAttribute('aria-label', '关闭通知');
        close.textContent = '×';

        let timer = null;
        const remove = () => {
            if (timer) window.clearTimeout(timer);
            toast.classList.remove('is-visible');
            window.setTimeout(() => toast.remove(), 240);
        };
        close.addEventListener('click', remove);
        toast.append(icon, text, close);
        region.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('is-visible'));
        timer = window.setTimeout(remove, duration);
        return toast;
    }

    function setFeedback(idOrElement, message = '', type = 'info') {
        const element = typeof idOrElement === 'string' ? $(idOrElement) : idOrElement;
        if (!element) return;
        element.className = `inline-feedback${message ? ` is-${type}` : ''}`;
        element.textContent = message;
        element.hidden = !message;
    }

    function setButtonBusy(button, busy, busyText) {
        if (!button) return;
        if (busy) {
            if (!button.dataset.idleText) button.dataset.idleText = button.textContent.trim();
            button.disabled = true;
            button.setAttribute('aria-busy', 'true');
            if (busyText) button.textContent = busyText;
        } else {
            button.removeAttribute('aria-busy');
            if (button.dataset.idleText) button.textContent = button.dataset.idleText;
            button.disabled = false;
        }
    }

    function setAudioBlob(element, blob) {
        if (!element) return;
        if (element.dataset.blobUrl) URL.revokeObjectURL(element.dataset.blobUrl);
        const url = URL.createObjectURL(blob);
        element.dataset.blobUrl = url;
        element.src = url;
        element.hidden = false;
    }

    function resetAudioElement(element) {
        if (!element) return;
        element.pause();
        if (element.dataset.blobUrl) URL.revokeObjectURL(element.dataset.blobUrl);
        delete element.dataset.blobUrl;
        element.removeAttribute('src');
        element.load();
        element.hidden = true;
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

    function updateThemeButton() {
        const dark = document.documentElement.dataset.theme === 'dark';
        if ($('theme-icon')) $('theme-icon').textContent = dark ? '☀' : '◐';
        if ($('theme-label')) $('theme-label').textContent = dark ? '亮色' : '暗色';
        if ($('theme-button')) $('theme-button').setAttribute('aria-label', dark ? '切换到亮色主题' : '切换到暗色主题');
    }

    function toggleTheme() {
        const dark = document.documentElement.dataset.theme === 'dark';
        const next = dark ? 'light' : 'dark';
        document.documentElement.dataset.theme = next;
        try { localStorage.setItem('tts-theme', next); } catch (_) {}
        updateThemeButton();
    }

    async function setAdminToken() {
        const current = getToken();
        const value = window.prompt('请输入管理令牌（留空将移除当前浏览器中的令牌）', current);
        if (value === null) return;
        const token = value.trim();
        try {
            if (token) localStorage.setItem('tts-admin-token', token);
            else localStorage.removeItem('tts-admin-token');
        } catch (_) {
            notify('浏览器无法保存管理令牌', 'error');
            return;
        }
        state.authPrompted = false;
        notify(token ? '管理令牌已更新' : '管理令牌已移除', 'success');
        window.setTimeout(() => window.location.reload(), 250);
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
            const defaultOpen = name === 'setup';
            const open = hasOwn(preferences, name) ? Boolean(preferences[name]) : defaultOpen;
            setSectionOpen(panel, open, false, false);
            panel.querySelector('.panel-toggle').addEventListener('click', () => {
                setSectionOpen(panel, !panel.classList.contains('is-open'));
            });
        });
    }

    function credentialValue(field) {
        const element = document.querySelector(`[data-credential-field="${field}"]`);
        return element ? element.value.trim() : '';
    }

    function hasCredential(field) {
        return Boolean(credentialValue(field) || state.configuredFields[field]);
    }

    function validateProviderDraft(provider = state.provider) {
        const voices = state.voiceCatalogs[provider];
        if (state.voiceLoading || !voices) return '请等待当前服务商的音色列表加载完成。';
        if (!voices.some(voice => voice.id === state.selectedVoices[provider])) return '当前音色无效，请重新选择。';
        if (provider === 'fishaudio' && state.selectedVoices.fishaudio === 'custom'
            && !$('fishaudio-reference-id').value.trim()) {
            return '自定义 Fish Audio 音色需要填写 Reference ID。';
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
        data.fishaudio_reference_id = $('fishaudio-reference-id') ? $('fishaudio-reference-id').value.trim() : '';
        return JSON.stringify(data);
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
        const payload = {
            provider: state.provider,
            fishaudio_reference_id: $('fishaudio-reference-id') ? $('fishaudio-reference-id').value.trim() : '',
        };
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
        const credentialChanged = $$('[data-credential-field]').some(element => Boolean(element.value.trim()));
        state.dirty = state.needsProviderRepair || credentialChanged || coreSnapshot() !== state.baselineCore;
        const chip = $('save-chip');
        chip.classList.toggle('is-saved', !state.dirty);
        chip.classList.toggle('is-dirty', state.dirty);
        $('save-chip-text').textContent = state.dirty ? '有未保存更改' : '已保存';
        $('save-state-title').textContent = state.dirty ? '当前设置尚未保存' : '所有更改均已保存';
        $('save-state-description').textContent = state.dirty
            ? '可以先测试当前草稿，确认后再保存到服务器。'
            : '切换服务商和音色不会立即影响服务器。';
        const saveButton = $('save-button');
        if (!saveButton.hasAttribute('aria-busy')) saveButton.disabled = !state.dirty || state.voiceLoading;
        const testButton = $('test-config-button');
        if (!testButton.hasAttribute('aria-busy')) testButton.disabled = state.voiceLoading;
        const testTtsButton = $('test-tts-button');
        if (!testTtsButton.hasAttribute('aria-busy')) {
            testTtsButton.disabled = state.voiceLoading || !(state.voiceCatalogs[state.provider] || []).length;
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
        Object.keys(PROVIDERS).forEach(provider => {
            const element = $(`${provider}-status`);
            if (!element) return;
            const status = state.providerStatus[provider] || {};
            const ready = provider === 'edge' || Boolean(status.ready);
            element.textContent = provider === 'edge' ? '免费' : (ready ? '已配置' : '待配置');
            element.classList.toggle('is-ready', ready);
            element.classList.toggle('is-missing', !ready);
        });
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
        const voice = voiceName(currentVoiceId());
        $('setup-summary').textContent = `${providerMeta().short} · ${voice || '未选择音色'}`;
        $('active-provider-name').textContent = providerMeta().name;
        $('stats-provider-title').textContent = `${providerMeta().name} 使用量`;
    }

    function updateFishReferenceState() {
        const input = $('fishaudio-reference-id');
        if (!input) return;
        const customSelected = state.selectedVoices.fishaudio === 'custom';
        input.disabled = !customSelected;
        input.title = customSelected ? '' : '选择 Fish 自定义音色后可编辑';
    }

    function applyProvider(provider, markDirty = true) {
        if (!hasOwn(PROVIDERS, provider)) return;
        const changed = state.provider !== provider;
        state.provider = provider;
        if (changed) clearSynthesisOutputs();
        $$('.provider-option').forEach(button => {
            const active = button.dataset.provider === provider;
            button.classList.toggle('is-active', active);
            button.setAttribute('aria-checked', String(active));
            button.tabIndex = active ? 0 : -1;
        });
        $$('[data-settings-provider]').forEach(panel => {
            panel.classList.toggle('is-active', panel.dataset.settingsProvider === provider);
        });
        renderCurrentCredentialState();
        updateFishReferenceState();
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
        if (!$('preview-button').hasAttribute('aria-busy')) $('preview-button').disabled = !selectedId || !list.length;
        $('all-voices-button').disabled = !list.length;
        $('voice-count').textContent = query ? `${matches.length} / ${list.length} 个音色` : `${list.length} 个音色`;
        fillCompareSelects(list);
        updateFishReferenceState();
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
        const requestId = ++state.voiceRequestId;
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
            if (requestId !== state.voiceRequestId || provider !== state.provider) return;
            state.voiceLoading = false;
            state.voiceCatalogs[provider] = voices;
            if (!voices.some(voice => voice.id === state.selectedVoices[provider]) && voices.length) {
                state.selectedVoices[provider] = voices[0].id;
                if (provider === state.savedProvider) state.voiceTouched.add(provider);
                else normalizeBaselineVoice(provider, voices[0].id);
            }
            renderVoiceSelect();
            updateDirtyState();
            updateLegadoConfig();
        } catch (error) {
            if (requestId !== state.voiceRequestId || provider !== state.provider) return;
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

    async function updateLegadoConfig() {
        const voice = currentVoiceId();
        if (!voice) return;
        const requestId = ++state.configRequestId;
        const code = $('legado-config');
        code.textContent = '正在生成配置…';
        $('copy-config-button').disabled = true;
        $('copy-subscribe-button').disabled = true;
        setFeedback('integration-feedback');
        try {
            const data = await jsonRequest(`/api/legado/config?voice=${encodeURIComponent(voice)}`);
            if (requestId !== state.configRequestId || voice !== currentVoiceId()) return;
            state.legadoConfigText = JSON.stringify(data, null, 2);
            code.textContent = state.legadoConfigText;
            if (!$('copy-config-button').hasAttribute('aria-busy')) $('copy-config-button').disabled = false;
            if (!$('copy-subscribe-button').hasAttribute('aria-busy')) $('copy-subscribe-button').disabled = false;
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
        const voice = currentVoiceId();
        if (!voice) return;
        const button = $('copy-subscribe-button');
        setButtonBusy(button, true, '生成中');
        try {
            const data = await jsonRequest(`/api/legado/subscribe?voice=${encodeURIComponent(voice)}`);
            const url = data.url || `${window.location.origin}/api/legado/subscribe?voice=${encodeURIComponent(voice)}&auto=true`;
            await copyText(url);
            notify('订阅链接已复制', 'success');
            setFeedback('integration-feedback', '订阅链接已复制，可直接导入开源阅读。', 'success');
        } catch (error) {
            setFeedback('integration-feedback', `复制订阅链接失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
            button.disabled = !currentVoiceId() || !(state.voiceCatalogs[state.provider] || []).length;
        }
    }

    async function synthesize(text, voice) {
        const response = await expectOk(await api('/speech/stream', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text, voice, rate: '0%'}),
        }));
        return {response, blob: await response.blob()};
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
        setButtonBusy(button, true, '正在生成');
        setFeedback('voice-feedback', '正在生成试听音频…', 'info');
        try {
            const result = await synthesize('你好，我是您的朗读助手，很高兴认识您。', voice);
            if (requestId !== state.previewRequestId) return;
            const player = $('preview-player');
            setAudioBlob(player, result.blob);
            const feedback = synthesisFeedback(result, '试听音频已生成。');
            setFeedback('voice-feedback', feedback.message, feedback.type);
            await playAudio(player, 'voice-feedback');
        } catch (error) {
            if (requestId !== state.previewRequestId) return;
            setFeedback('voice-feedback', `试听失败：${error.message}`, 'error');
        } finally {
            if (requestId === state.previewRequestId) {
                setButtonBusy(button, false);
                button.disabled = !currentVoiceId() || !(state.voiceCatalogs[state.provider] || []).length;
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
        resetAudioElement($('test-audio-player'));
        setButtonBusy(button, true, '生成中');
        setFeedback('test-feedback', '正在生成音频…', 'info');
        try {
            const result = await synthesize(text, voice);
            if (requestId !== state.testRequestId) return;
            const player = $('test-audio-player');
            setAudioBlob(player, result.blob);
            const feedback = synthesisFeedback(result, '测试音频已生成。');
            setFeedback('test-feedback', feedback.message, feedback.type);
            await playAudio(player, 'test-feedback');
        } catch (error) {
            if (requestId !== state.testRequestId) return;
            setFeedback('test-feedback', `生成失败：${error.message}`, 'error');
        } finally {
            if (requestId === state.testRequestId) setButtonBusy(button, false);
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
        setButtonBusy(button, true, '生成中');
        setFeedback('compare-feedback', '正在同时生成两个音频…', 'info');
        try {
            const [resultA, resultB] = await Promise.all([synthesize(text, voiceA), synthesize(text, voiceB)]);
            if (requestId !== state.compareRequestId) return;
            setAudioBlob($('audio-a'), resultA.blob);
            setAudioBlob($('audio-b'), resultB.blob);
            const fallback = [resultA, resultB].some(result => result.response.headers.get('X-TTS-Fallback') === 'true');
            setFeedback('compare-feedback', fallback
                ? '对比音频已生成，但至少一个音色触发了 Edge 回退，请结合连接测试判断配置。'
                : '两个对比音频均已生成。', fallback ? 'warning' : 'success');
        } catch (error) {
            if (requestId !== state.compareRequestId) return;
            setFeedback('compare-feedback', `对比失败：${error.message}`, 'error');
        } finally {
            if (requestId === state.compareRequestId) setButtonBusy(button, false);
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
        element.className = `service-pill is-${kind}`;
        $('service-status-text').textContent = text;
    }

    function renderHealth(health, metrics = null) {
        const cache = health.cache || {};
        $('cache-count').textContent = Number(cache.count || cache.size || 0).toLocaleString();
        $('cache-memory').textContent = `${(Number(cache.bytes || 0) / (1024 * 1024)).toFixed(1)} MB`;
        $('ffmpeg-status').textContent = health.ffmpeg_available ? '可用' : '不可用';
        $('ffmpeg-status').style.color = health.ffmpeg_available ? 'var(--success)' : 'var(--warning)';
        $('admin-status').textContent = health.admin_protected ? '已启用' : '未启用';
        $('admin-status').style.color = health.admin_protected ? 'var(--success)' : 'var(--text-soft)';
        if (metrics) {
            $('total-chars-all').textContent = Number(metrics.totalChars || 0).toLocaleString();
            $('total-requests-all').textContent = Number(metrics.totalRequests || 0).toLocaleString();
            $('cache-hit-rate').textContent = `${(Number(metrics.cacheHitRate || 0) * 100).toFixed(1)}%`;
            $('response-time-p95').textContent = `${Math.round(Number(metrics.p95 || 0))} ms`;
        }
        setServiceStatus('online', `服务正常 · v${health.version || INITIAL.version || ''}`);
    }

    async function loadHealthOnly() {
        try {
            const health = await jsonRequest('/health');
            renderHealth(health);
        } catch (error) {
            setServiceStatus('error', '服务状态异常');
            if (isSectionOpen('monitoring')) setFeedback('monitor-feedback', `健康状态加载失败：${error.message}`, 'error');
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
        $('activity-empty').hidden = true;
        const line = document.createElement('div');
        line.className = `activity-line${data.status >= 200 && data.status < 300 ? '' : ' is-error'}`;
        const time = document.createElement('span');
        time.textContent = (data.ts || '').split('T')[1]?.split('.')[0] || '刚刚';
        const provider = document.createElement('span');
        provider.textContent = data.provider || '未知';
        const voice = document.createElement('span');
        voice.textContent = data.voice || '—';
        const chars = document.createElement('span');
        chars.textContent = `${Number(data.chars || 0)} 字`;
        const status = document.createElement('span');
        status.className = data.status >= 200 && data.status < 300 ? 'activity-ok' : '';
        status.textContent = `${data.status || '—'} · ${Math.round(Number(data.ms || 0))}ms`;
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
        setLiveState('connecting', '正在连接');
        const query = token ? `?token=${encodeURIComponent(token)}` : '';
        const source = new EventSource(`/api/events${query}`);
        state.activitySource = source;
        source.onopen = () => setLiveState('live', '实时连接');
        source.onmessage = event => {
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'connected') setLiveState('live', '实时连接');
                if (data.type === 'tts_request') appendActivity(data);
            } catch (_) {}
        };
        source.onerror = () => {
            if (state.activitySource === source) setLiveState('connecting', '连接中断，重试中');
        };
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
            if (!window.confirm(warning)) return;
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

    async function resetStats() {
        if (!window.confirm('确定重置全部服务商的累计统计吗？此操作无法撤销。')) return;
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

    function clearModalAudio() {
        if (state.modalAudio) {
            state.modalAudio.pause();
            state.modalAudio.removeAttribute('src');
        }
        if (state.modalAudioUrl) URL.revokeObjectURL(state.modalAudioUrl);
        state.modalAudio = null;
        state.modalAudioUrl = '';
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
        clearSynthesisOutputs();
        state.selectedVoices[state.provider] = voiceId;
        state.voiceTouched.add(state.provider);
        $('voice-search').value = '';
        renderVoiceSelect();
        updateDirtyState();
        updateLegadoConfig();
        closeVoiceDialog();
        notify(`已选择音色：${voiceName(voiceId)}`, 'success');
    }

    async function previewLibraryVoice(voice, button) {
        const requestId = ++state.modalPreviewId;
        setButtonBusy(button, true, '加载');
        try {
            clearModalAudio();
            const result = await synthesize('你好，我是这个音色的朗读效果。', voice.id);
            if (requestId !== state.modalPreviewId || !$('voice-dialog').open) return;
            const url = URL.createObjectURL(result.blob);
            const audio = new Audio(url);
            state.modalAudio = audio;
            state.modalAudioUrl = url;
            await audio.play();
            const fallback = result.response.headers.get('X-TTS-Fallback') === 'true';
            if (fallback) notify('试听已回退到 Edge，当前服务商可能未配置成功', 'warning', 5000);
        } catch (error) {
            if (requestId !== state.modalPreviewId || !$('voice-dialog').open) return;
            notify(`试听失败：${error.message}`, 'error');
        } finally {
            setButtonBusy(button, false);
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
            card.className = `voice-card${voice.id === currentVoiceId() ? ' is-current' : ''}`;
            const copy = document.createElement('span');
            copy.className = 'voice-card-copy';
            const name = document.createElement('strong');
            name.textContent = voice.name;
            const id = document.createElement('small');
            id.textContent = voice.id;
            copy.append(name, id);

            const actions = document.createElement('span');
            actions.className = 'voice-card-actions';
            const preview = document.createElement('button');
            preview.className = 'button ghost';
            preview.type = 'button';
            preview.textContent = '试听';
            preview.addEventListener('click', () => previewLibraryVoice(voice, preview));
            const use = document.createElement('button');
            use.className = 'button secondary';
            use.type = 'button';
            use.textContent = voice.id === currentVoiceId() ? '已选择' : '使用';
            use.disabled = voice.id === currentVoiceId();
            use.addEventListener('click', () => chooseVoiceFromLibrary(voice.id));
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
        const buttons = $$('.provider-option');
        const current = buttons.indexOf(event.currentTarget);
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
        if ($('token-button')) $('token-button').addEventListener('click', setAdminToken);
        if ($('maintenance-token-button')) $('maintenance-token-button').addEventListener('click', setAdminToken);
        $$('.provider-option').forEach(button => {
            button.addEventListener('click', () => applyProvider(button.dataset.provider));
            button.addEventListener('keydown', handleProviderKeydown);
        });
        $('settings-form').addEventListener('submit', event => {
            event.preventDefault();
            if (state.dirty) saveConfig();
        });

        $('voice-search').addEventListener('input', renderVoiceSelect);
        $('voice-select').addEventListener('change', event => {
            if (!event.currentTarget.value) return;
            clearSynthesisOutputs();
            state.selectedVoices[state.provider] = event.currentTarget.value;
            state.voiceTouched.add(state.provider);
            updateFishReferenceState();
            updateDirtyState();
            updateSetupSummary();
            updateLegadoConfig();
        });
        $('preview-button').addEventListener('click', previewVoice);
        $('all-voices-button').addEventListener('click', openVoiceDialog);
        $('copy-config-button').addEventListener('click', copyConfig);
        $('copy-subscribe-button').addEventListener('click', copySubscribeUrl);
        $('save-button').addEventListener('click', saveConfig);
        $('test-config-button').addEventListener('click', testConfig);
        $('test-tts-button').addEventListener('click', testTTS);
        $('compare-button').addEventListener('click', compareVoices);
        $('refresh-monitor-button').addEventListener('click', () => refreshMonitoring(true));
        $('export-button').addEventListener('click', exportConfig);
        $('import-button').addEventListener('click', () => $('import-file').click());
        $('import-file').addEventListener('change', importConfig);
        $('reset-stats-button').addEventListener('click', resetStats);

        $$('[data-credential-field], [data-config-field]').forEach(element => {
            element.addEventListener('input', () => {
                setFeedback('settings-feedback');
                updateDirtyState();
            });
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
            stopActivityStream();
            stopModalAudio();
            $$('audio[data-blob-url]').forEach(audio => URL.revokeObjectURL(audio.dataset.blobUrl));
        });
    }

    async function initialize() {
        updateThemeButton();
        initCollapsibles();
        let protectedConfig = null;
        let protectedConfigError = '';
        if (INITIAL.adminProtected && getToken()) {
            try {
                protectedConfig = await jsonRequest('/api/config');
                if (protectedConfig.provider_status) state.providerStatus = protectedConfig.provider_status;
                if (protectedConfig.configured_fields) state.configuredFields = protectedConfig.configured_fields;
            } catch (error) {
                protectedConfigError = error.message;
            }
        }
        bindEvents();
        if ($('fishaudio-reference-id')) {
            const referenceId = protectedConfig && typeof protectedConfig.fishaudio_reference_id === 'string'
                ? protectedConfig.fishaudio_reference_id
                : INITIAL.fishaudioReferenceId || '';
            $('fishaudio-reference-id').value = referenceId;
        }
        state.baselineCore = coreSnapshot();
        renderProviderStatuses();
        updateCredentialPlaceholders();
        applyProvider(state.provider, false);
        updateDirtyState();
        loadHealthOnly();

        if (invalidInitialProvider) {
            setFeedback('settings-feedback', `已保存的服务商 “${INITIAL.provider}” 无法识别，界面已安全回退到 Edge；保存后可修复配置。`, 'warning');
            notify('检测到无效的旧服务商配置', 'warning', 6000);
        } else if (INITIAL.adminProtected && !getToken()) {
            setFeedback('settings-feedback', '本服务已启用管理保护；保存、测试和维护操作需要先设置管理令牌。', 'warning');
        } else if (protectedConfigError) {
            setFeedback('settings-feedback', `无法读取受保护的设置：${protectedConfigError}`, 'warning');
        }
        if (isSectionOpen('monitoring')) startMonitoring();
    }

    initialize().catch(error => {
        setFeedback('settings-feedback', `界面初始化失败：${error.message}`, 'error');
        notify('界面初始化失败', 'error', 6000);
    });
})();
