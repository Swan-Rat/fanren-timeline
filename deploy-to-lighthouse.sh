#!/bin/bash
# 凡人修仙传资料站 → 轻量服务器 /fan-ren/ 部署脚本
#
# 服务器：lhins-jnc075fm (62.234.17.62，ap-beijing)
# 架构：宿主 nginx gateway 把 /fan-ren/ 反代到 127.0.0.1:8081 → Docker 容器 frxxz-web(nginx:alpine)
#       容器挂载 /opt/frxxz/html -> /usr/share/nginx/html，即 WEBROOT=/opt/frxxz/html
#       （找路径别用 grep alias|root /etc/nginx/ —— 是 proxy_pass 查不到，用 docker inspect frxxz-web）
#
# ⚠️ 本机 SSH 未信任（Permission denied (publickey,password)），服务器侧执行只能走
#    Lighthouse MCP execute_command(TAT)。本脚本不直连服务器，只做两件本地事：
#      1) 生成 TAT 载荷（清单实时算自 site/，含 md5/size 断言，可重复执行）
#      2) 公网验证已上线内容
#    把载荷粘到 execute_command 的 Command 字段下发，再跑第 2 步验证。
#
# ⚠️ 发布源不会自动跟随本地文件：改完 site/ 必须先重新发布，服务器才拉得到新内容。
#    重新发布：workbuddy_sites_deploy(directory=site/, language=static, entryHtml=index.html)
#    两个域名指向同一应用、内容一致：
#      https://fanren-timeline-site.app.workbuddy.host   （语义化，推荐）
#      https://2108590721720340480.app.workbuddy.host    （首次发布分配的数字 ID）
#
# 用法：
#   bash deploy-to-lighthouse.sh gen      # 只生成 TAT 载荷
#   bash deploy-to-lighthouse.sh verify   # 只做公网验证
#   bash deploy-to-lighthouse.sh          # gen + verify（默认）
set -euo pipefail
cd "$(dirname "$0")"

SRC="https://fanren-timeline-site.app.workbuddy.host"
HOST="62.234.17.62"
SITE="site"
WEBROOT="/opt/frxxz/html"
PAYLOAD="${PAYLOAD:-/tmp/tat_deploy_payload.txt}"
MODE="${1:-all}"

do_gen() {
  echo "==> 生成 TAT 载荷"
  python3 gen_tat_payload.py --site "$SITE" --src "$SRC" --webroot "$WEBROOT" -o "$PAYLOAD"
  echo "  下一步：把 $PAYLOAD 全文作为 Command 下发到 execute_command（SystemType=Linux）"
  echo "  下发后每个文件应输出一行 OK（已更新）或 SKIP（md5 未变）"
  echo
}

do_verify() {
  echo "==> 公网验证（$SITE/ 为期望基线）"
  local fail=0 name enc url code got_md5 got_size want_md5 want_size
  for path in "$SITE"/*; do
    [ -f "$path" ] || continue
    name="$(basename "$path")"
    want_md5="$(md5 -q "$path")"
    want_size="$(wc -c < "$path" | tr -d ' ')"
    # 中文文件名走 URL 编码
    enc="$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$name")"
    url="http://$HOST/fan-ren/$enc?v=$(date +%s%N)"
    code="$(curl -sS -m 60 -o /tmp/_pub.bin -w '%{http_code}' "$url" || echo 000)"
    got_md5="$(md5 -q /tmp/_pub.bin)"
    got_size="$(wc -c < /tmp/_pub.bin | tr -d ' ')"
    if [ "$code" = 200 ] && [ "$got_md5" = "$want_md5" ] && [ "$got_size" = "$want_size" ]; then
      echo "  ✅ $name  $code  ${got_size}B  md5 一致"
    else
      echo "  ❌ $name  $code  ${got_size}B  md5=$got_md5 (期望 $want_md5 ${want_size}B)"
      fail=1
    fi
  done
  [ "$fail" = 1 ] && { echo "验证失败"; exit 1; }
  echo
  echo "全部通过。入口：http://$HOST/fan-ren/"
  echo "  灵界篇时间线 http://$HOST/fan-ren/timeline.html"
  echo "  仙界篇时间线 http://$HOST/fan-ren/xianjie-timeline.html"
  echo "  灵界地图     http://$HOST/fan-ren/map.html"
}

case "$MODE" in
  gen)    do_gen ;;
  verify) do_verify ;;
  all)    do_gen; do_verify ;;
  *)      echo "用法：$0 [gen|verify]"; exit 2 ;;
esac
