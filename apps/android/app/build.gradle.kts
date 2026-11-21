import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

// The Samsung Health Data SDK AAR is distributed only to signed-in Samsung developers and cannot be redistributed.
// Place it at app/libs/samsung-health-data-api.aar to compile the real Samsung adapter; otherwise a stub is used.
val samsungAar = file("libs/samsung-health-data-api.aar")
val hasSamsungSdk = samsungAar.exists()

// On-phone engine: the desktop's Python analytics engine running in the app through Chaquopy (Python 3.10).
// Off by default so the plain companion builds anywhere. CI enables it with -Pvitalis.engine=true after building the
// two Android wheels it needs (see python-wheels/README.md); -Pvitalis.abis picks the ABIs (default arm64-v8a).
val withEngine = providers.gradleProperty("vitalis.engine").orNull?.toBoolean() ?: false
val engineAbis = (providers.gradleProperty("vitalis.abis").orNull ?: "arm64-v8a").split(",").map { it.trim() }
val engineSources = rootProject.file("../../services/engine/vitalis_engine")
val stagedPython = layout.buildDirectory.dir("generated/vitalis-python")
if (withEngine) apply(plugin = "com.chaquo.python")

// Optional release signing from keystore.properties (never committed). Without it, release builds are unsigned.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "app.vitalis.companion"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.vitalis.companion"
        minSdk = 28
        targetSdk = 36
        versionCode = 2
        versionName = "0.1.1"
        buildConfigField("boolean", "HAS_SAMSUNG_SDK", hasSamsungSdk.toString())
        buildConfigField("boolean", "HAS_ENGINE", withEngine.toString())
        if (withEngine) ndk { abiFilters += engineAbis }
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    signingConfigs {
        if (keystoreProps.getProperty("storeFile") != null) {
            create("release") {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    sourceSets {
        getByName("main").java.srcDir(if (hasSamsungSdk) "src/samsung/java" else "src/samsungStub/java")
        getByName("main").java.srcDir(if (withEngine) "src/engine/java" else "src/engineStub/java")
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
    testOptions { unitTests.isReturnDefaultValues = true }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

if (withEngine) {
    // The engine package is copied (without caches) so only vitalis_engine — not tests or virtualenvs — is bundled.
    val stageEngine = tasks.register<Sync>("stageVitalisEngine") {
        from(engineSources) { exclude("**/__pycache__/**", "**/*.pyc") }
        into(stagedPython.map { it.dir("vitalis_engine") })
    }
    extensions.configure<com.chaquo.python.ChaquopyExtension>("chaquopy") {
        defaultConfig {
            version = "3.10"
            pip {
                // CI-built Android wheels: python-wheels/dist and its per-package subfolders (pip does not recurse).
                val wheels = rootProject.file("python-wheels/dist")
                (listOf(wheels) + (wheels.listFiles()?.filter { it.isDirectory }?.sorted() ?: emptyList()))
                    .forEach { options("--find-links", it.absolutePath) }
                install("-r", rootProject.file("python-wheels/requirements-phone.txt").absolutePath)
            }
        }
        sourceSets.getByName("main").srcDir(stagedPython)
    }
    tasks.matching { it.name.startsWith("merge") && it.name.endsWith("PythonSources") }.configureEach { dependsOn(stageEngine) }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2026.06.00")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.13.0")
    implementation("androidx.core:core-ktx:1.18.0")
    implementation("androidx.health.connect:connect-client:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.12.0")
    implementation("com.squareup.okhttp3:okhttp:5.4.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.13.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation("com.journeyapps:zxing-android-embedded:4.3.0")
    if (hasSamsungSdk) {
        implementation(files(samsungAar))
        implementation("com.google.code.gson:gson:2.15.0")
    }
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.3.0")
    androidTestImplementation("androidx.test:runner:1.7.0")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.11.0")
}
