#!/usr/bin/env bash
# Build the tiny Android shell without Gradle: aapt2 + javac + d8 + apksigner.
#
#   ./build.sh                # -> dist/AG-Deck.apk
#   ANDROID_HOME=~/Android/Sdk ./build.sh
#
# Requires: Android SDK (build-tools + one platform), JDK 17+.
set -euo pipefail
cd "$(dirname "$0")"

SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/android-sdk}}"
BT="$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1 || true)"
PLATFORM="$(ls -d "$SDK"/platforms/android-* 2>/dev/null | sort -V | tail -1 || true)"
AJAR="${PLATFORM:+$PLATFORM/android.jar}"

if [ -z "$BT" ] || [ -z "$AJAR" ] || [ ! -e "$AJAR" ]; then
  echo "找不到 Android SDK（build-tools / platforms）。" >&2
  echo "请安装 Android SDK，或设置 ANDROID_HOME 指向它。" >&2
  exit 1
fi

for tool in aapt2 d8 zipalign apksigner; do
  [ -x "$BT/$tool" ] || { echo "缺少构建工具：$BT/$tool" >&2; exit 1; }
done

PKG=io.agdeck.console
OUT=dist/AG-Deck.apk
KS=keystore/debug.jks
ALIAS=agdeck

rm -rf build
mkdir -p build/gen build/obj build/dex dist keystore

echo "→ SDK        $SDK"
echo "→ build-tools $(basename "$BT")"

echo "→ 编译资源"
"$BT/aapt2" compile --dir res -o build/res.zip

echo "→ 链接资源"
"$BT/aapt2" link \
  -o build/base.apk \
  -I "$AJAR" \
  --manifest AndroidManifest.xml \
  --java build/gen \
  --min-sdk-version 26 \
  --target-sdk-version 34 \
  --version-code 1 --version-name 1.0.0 \
  build/res.zip

echo "→ 编译 Java"
# shellcheck disable=SC2046
javac --release 8 -nowarn -cp "$AJAR" -d build/obj \
  $(find src build/gen -name '*.java')

echo "→ 转 dex"
# d8 直接吃一堆 .class 会崩：先打成 jar，且输出目录必须已存在
(cd build/obj && jar cf ../classes.jar .)
"$BT/d8" --lib "$AJAR" --min-api 26 --output build/dex build/classes.jar

echo "→ 打包"
(cd build/dex && zip -q -u ../base.apk classes.dex)
"$BT/zipalign" -f -p 4 build/base.apk build/aligned.apk

if [ ! -f "$KS" ]; then
  echo "→ 生成调试签名证书（keystore/debug.jks，发布时请换成你自己的）"
  keytool -genkeypair -v -keystore "$KS" -storepass android -keypass android \
    -alias "$ALIAS" -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=AG Deck, OU=dev, O=AG Deck, C=CN" >/dev/null 2>&1
fi

echo "→ 签名"
"$BT/apksigner" sign \
  --ks "$KS" --ks-pass pass:android --key-pass pass:android --ks-key-alias "$ALIAS" \
  --out "$OUT" build/aligned.apk

"$BT/apksigner" verify --print-certs "$OUT" | head -4
ls -lh "$OUT"
echo "✓ 完成：$(pwd)/$OUT"
