# Keep AndroidViewModel constructor invoked via reflection by ViewModelProvider.
-keepclassmembers class * extends androidx.lifecycle.AndroidViewModel {
    public <init>(android.app.Application);
}

# Keep Kotlin metadata needed by Compose / Coroutines runtime.
-keepattributes RuntimeVisibleAnnotations,Signature,InnerClasses,EnclosingMethod

# Silence harmless optional/desktop annotations if referenced by KMP artifacts.
-dontwarn org.jetbrains.annotations.**
-dontwarn java.lang.invoke.**
