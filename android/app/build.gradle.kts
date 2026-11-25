plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "io.github.legadotts.client"
    compileSdk = 37
    defaultConfig {
        applicationId = "io.github.legadotts.client"
        minSdk = 28
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "io.github.legadotts.client.ContractRunner"
    }
    signingConfigs {
        val storePath = System.getenv("RELEASE_STORE_FILE")?.takeIf { it.isNotBlank() }
        if (storePath != null) {
            create("release") {
                storeFile = file(storePath)
                storePassword = System.getenv("RELEASE_STORE_PASSWORD")
                keyAlias = System.getenv("RELEASE_KEY_ALIAS")
                keyPassword = System.getenv("RELEASE_KEY_PASSWORD")
            }
        }
    }
    buildTypes {
        release {
            // Full R8 optimization & shrinking: removes debug Compose source info, inlines lambdas
            // and value classes, and yields ~5x smoother animations and a much smaller APK.
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            // Use custom release keystore when provided, otherwise fall back to debug signing so
            // the optimized release APK remains directly installable out of the box.
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    buildFeatures { compose = true }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.10.0")
    // The app manifest configures this provider to opt out of downloadable emoji fonts.
    implementation("androidx.startup:startup-runtime:1.1.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    implementation("top.yukonga.miuix.kmp:miuix-ui-android:0.9.3")
    testImplementation("junit:junit:4.13.2")
}
