#!/usr/bin/env bash
# 让 tailscaled 走本机代理登录（Linux + systemd；适用于 Clash TUN 模式下控制面连不上）
# Linux-only helper: route tailscaled's control-plane traffic through a local proxy.
#
#   sudo bash packaging/tailscale-login.sh          # 默认代理 http://127.0.0.1:7897
#   sudo PROXY=http://127.0.0.1:7890 bash packaging/tailscale-login.sh
#   sudo bash packaging/tailscale-login.sh --off    # 移除代理配置（恢复直连）
#
set -euo pipefail

PROXY="${PROXY:-http://127.0.0.1:7897}"
DROPIN_DIR=/etc/systemd/system/tailscaled.service.d
DROPIN="$DROPIN_DIR/proxy.conf"

if [ "${1:-}" = "--off" ]; then
  rm -f "$DROPIN"
  rmdir "$DROPIN_DIR" 2>/dev/null || true
  systemctl daemon-reload
  systemctl restart tailscaled
  echo "✓ 已移除代理配置，tailscaled 恢复直连"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "需要 root：sudo bash $0" >&2
  exit 1
fi

echo "→ 使用代理：$PROXY"

if ! curl -sS -m 5 -o /dev/null -x "$PROXY" https://controlplane.tailscale.com/health 2>/dev/null; then
  echo "! 代理 $PROXY 连不上 controlplane，请确认 Clash 正在运行、端口正确" >&2
  echo "  可用：ss -lntp | grep -E '7890|7897' 查看监听端口" >&2
  exit 1
fi

mkdir -p "$DROPIN_DIR"
cat > "$DROPIN" <<EOF
# 由 antigravity-console/tools/tailscale-login.sh 生成
# 目的：让 tailscaled 的控制面流量走本机代理，避免 Clash TUN 的 fake-ip 干扰
[Service]
Environment="HTTPS_PROXY=$PROXY"
Environment="HTTP_PROXY=$PROXY"
Environment="NO_PROXY=localhost,127.0.0.1,100.64.0.0/10,*.ts.net"
EOF

echo "✓ 已写入 $DROPIN"
systemctl daemon-reload
systemctl restart tailscaled
sleep 3

echo
echo "→ 开始登录，等几秒会打印一个 https://login.tailscale.com/a/xxxx 链接"
echo "  在浏览器里打开它完成授权（浏览器本身也需要能访问外网）"
echo
set +e
tailscale up
RC=$?
set -e

echo
if [ $RC -eq 0 ]; then
  tailscale status | head -5
  echo
  echo "✓ 本机 Tailscale IP：$(tailscale ip -4 2>/dev/null | head -1)"
  echo "  控制台地址： http://$(tailscale ip -4 2>/dev/null | head -1):8848/"
else
  echo "! 登录没有完成，最近日志："
  journalctl -u tailscaled -n 8 --no-pager | tail -8
fi
