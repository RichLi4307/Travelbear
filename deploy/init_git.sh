#!/bin/bash
# 初始化 ICAN 仓库 git（上游基线 + 部署改动 两次提交）
# 前置：sudo apt install git
# 用法：bash deploy/init_git.sh
set -euo pipefail

REPO=/home/shu/ICAN
cd "$REPO"

if [ -d .git ]; then
    echo "git 已初始化过，跳过"
    exit 0
fi

echo "==> 1/5 备份当前工作树"
mv "$REPO" /tmp/ICAN_modified
cd /home/shu

echo "==> 2/5 原地恢复上游原始代码"
mkdir "$REPO"
unzip -q -o /home/shu/ICAN.zip -d /tmp/ican_upstream
mv /tmp/ican_upstream/ICAN/* "$REPO/" 2>/dev/null || true
mv /tmp/ican_upstream/ICAN/.* "$REPO/" 2>/dev/null || true
rm -rf /tmp/ican_upstream
# 根 .gitignore 随改动树走，先放回来保证基线提交就不含密钥/缓存
cp /tmp/ICAN_modified/.gitignore "$REPO/.gitignore"

echo "==> 3/5 基线提交（上游原始代码）"
cd "$REPO"
git init -b main
# 本仓库的提交身份（不污染全局配置）
git config user.name  "shu"
git config user.email "shu@bear-guide.local"
git add -A
git commit -q -m "导入上游原始代码（ICAN.zip 原样；.env 密钥与运行缓存不入库）"

echo "==> 4/5 叠回部署改动"
rsync -a --exclude=.git /tmp/ICAN_modified/ "$REPO/"
rm -rf /tmp/ICAN_modified

echo "==> 5/5 提交部署改动"
git add -A
git commit -q -m "部署适配: 三键映射/服务锁/打断提示音/音量控制/冻结问答/systemd单元（详见 CHANGELOG.md）"

echo "==> 完成，提交历史："
git log --oneline
