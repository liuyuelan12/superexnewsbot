# SuperEx News Bot 📰

自动从加密货币新闻源获取新闻，并广播到 Telegram 群组的 Bot。

## ✨ 功能特点

- 🔄 **自动获取新闻** - 从 CoinDesk、CoinTelegraph、Decrypt 等主流媒体获取最新新闻
- ⏰ **频率限制** - 每小时最多广播一条消息，避免刷屏
- 🚀 **Trade Now 按钮** - 每条消息附带交易按钮，引导用户到 SuperEx 交易
- 💾 **持久化存储** - 记录已发送的新闻，避免重复推送
- 🌐 **多源聚合** - 支持多个 RSS 新闻源

## 📋 命令列表

| 命令 | 说明 |
|:---|:---|
| `/start` | 在群组中激活 Bot |
| `/stop` | 停止接收新闻推送 |
| `/status` | 查看 Bot 状态 |
| `/news` | 手动获取最新新闻 |

## 🚀 快速开始

### 1. 安装依赖

```bash
cd /Users/ericc/Desktop/SuperEx/Bot/SuperExNewsBot
pip install -r requirements.txt
```

### 2. 运行 Bot

```bash
python bot.py
```

### 3. 在 Telegram 群组中使用

1. 将 `@SuperExNewsBot` 添加到群组
2. 授予 Bot 管理员权限（发送消息权限）
3. 发送 `/start` 激活

## ⚙️ 配置说明

编辑 `bot.py` 文件中的配置：

```python
# Bot Token
BOT_TOKEN = "your_bot_token"

# 交易链接
TRADE_URL = "https://www.superex.com/trade/BTC_USDT"

# 广播间隔（秒）- 默认1小时
BROADCAST_INTERVAL_SECONDS = 3600
```

## 📡 新闻源

目前支持的 RSS 新闻源：

| 来源 | 优先级 |
|:---|:---:|
| CoinDesk | 1 (最高) |
| CoinTelegraph | 2 |
| Decrypt | 3 |
| CryptoSlate | 4 |

## 📁 数据文件

Bot 会在 `data/` 目录下创建以下文件：

- `groups.json` - 已注册的群组列表
- `last_broadcast.json` - 上次广播时间
- `sent_news.json` - 已发送的新闻记录

## 🔧 后台运行

使用 `nohup` 或 `screen` 在后台运行：

```bash
# 使用 nohup
nohup python bot.py > bot.log 2>&1 &

# 使用 screen
screen -S superex-news-bot
python bot.py
# Ctrl+A, D 退出 screen
```

## 📝 日志

Bot 会自动输出运行日志，包括：
- 群组注册/取消注册事件
- 新闻获取状态
- 广播成功/失败信息
