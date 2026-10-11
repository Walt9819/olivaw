import java.util.Properties

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// The release key, read from a file kept outside the repository. Missing is not an
// error - a contributor who only ever builds debug should not need it.
val keystoreProperties = Properties()
// Looked for where Olivaw installs itself, not at a path baked into the repository:
// OLIVAW_SIGNING for anyone who keeps it elsewhere, then the usual per-user folder on
// Windows, macOS and Linux. Whoever clones this gets their own key, or no key at all.
val keystorePropertiesFile = listOfNotNull(
    System.getenv("OLIVAW_SIGNING"),
    System.getenv("LOCALAPPDATA")?.let { "$it/Olivaw/signing" },
    System.getenv("HOME")?.let { "$it/.olivaw/signing" },
).map { file("$it/key.properties") }.firstOrNull { it.exists() } ?: file("key.properties")
if (keystorePropertiesFile.exists()) {
    keystorePropertiesFile.inputStream().use { keystoreProperties.load(it) }
} else {
    logger.lifecycle("Olivaw: no release key on this machine, signing with the debug key.")
}

android {
    namespace = "com.olivaw.olivaw"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // TODO: Specify your own unique Application ID (https://developer.android.com/studio/build/application-id.html).
        applicationId = "com.olivaw.olivaw"
        // You can update the following values to match your application needs.
        // For more information, see: https://flutter.dev/to/review-gradle-config.
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        create("release") {
            // The key lives outside this repository, in the Olivaw install folder, and
            // is NEVER committed: whoever holds it can publish an update that Android
            // will install over this app without a word to the person running it.
            if (keystoreProperties.isNotEmpty()) {
                keyAlias = keystoreProperties["keyAlias"] as String
                keyPassword = keystoreProperties["keyPassword"] as String
                storeFile = file(keystoreProperties["storeFile"] as String)
                storePassword = keystoreProperties["storePassword"] as String
            }
        }
    }

    buildTypes {
        release {
            // The debug key is per-machine, so an APK signed with it can only ever be
            // updated from the machine that built it. Fall back to it only when the
            // real key is not on this machine, and say so in the build output.
            signingConfig = if (keystoreProperties.isNotEmpty())
                signingConfigs.getByName("release")
            else
                signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
