# kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-keepclassmembers class app.vitalis.companion.** { *** Companion; }
-keepclasseswithmembers class app.vitalis.companion.** { kotlinx.serialization.KSerializer serializer(...); }
# Samsung Health Data SDK (only when the AAR is present)
-keep class com.samsung.android.sdk.health.data.** { *; }
-dontwarn com.samsung.android.sdk.health.data.**
