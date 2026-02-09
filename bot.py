#!/usr/bin/env python3
"""
SuperEx News Bot
Fetches crypto news from RSS feeds and broadcasts to Telegram groups
Maximum one broadcast per hour, with Trade Now button
"""

import asyncio
import json
import logging
import os
import re
import ssl
import time
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp
import feedparser
from aiohttp_socks import ProxyConnector
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, ChatMemberUpdated
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, ChatMemberHandler, filters
from telegram.constants import ChatMemberStatus

# ==================== Configuration ====================
BOT_TOKEN = "8350592308:AAF_EmNujrt4kgdzNXa35PRFQ2rJFsat1i0"
TRADE_URL = "https://www.superex.com/trade/BTC_USDT"
BROADCAST_INTERVAL_SECONDS = 3600  # Max one broadcast per hour (3600 seconds)

# Proxy Configuration - SOCKS5 proxy list (will try until one works)
PROXY_LIST = [
    "socks5://VYHMOLXmzmCy:X9FgH374SH@50.3.54.17:443",
    "socks5://zhouyunhua0628:pzBLnbDWjs@66.93.164.245:50101",
]

# Data storage paths
DATA_DIR = Path(__file__).parent / "data"
GROUPS_FILE = DATA_DIR / "groups.json"
LAST_BROADCAST_FILE = DATA_DIR / "last_broadcast.json"
SENT_NEWS_FILE = DATA_DIR / "sent_news.json"

# RSS News Sources
RSS_FEEDS = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "priority": 1
    },
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
        "priority": 2
    },
    {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "priority": 3
    },
    {
        "name": "CryptoSlate",
        "url": "https://cryptoslate.com/feed/",
        "priority": 4
    },
]

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==================== Permission Check ====================
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user is group admin or owner"""
    chat = update.effective_chat
    user = update.effective_user
    
    # Allow all commands in private chat
    if chat.type == "private":
        return True
    
    # Check admin status in groups
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        logger.error(f"Failed to check admin status: {e}")
        return False


async def admin_required(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if admin permission is required"""
    if not await is_admin(update, context):
        await update.message.reply_text(
            "⛔ Only group admins can use this command",
            parse_mode="HTML"
        )
        return False
    return True


# ==================== Data Management ====================
def ensure_data_dir():
    """Ensure data directory exists"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_groups() -> set:
    """Load registered group IDs"""
    ensure_data_dir()
    if GROUPS_FILE.exists():
        with open(GROUPS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_groups(groups: set):
    """Save group IDs"""
    ensure_data_dir()
    with open(GROUPS_FILE, "w") as f:
        json.dump(list(groups), f)


def load_last_broadcast() -> float:
    """Load last broadcast timestamp"""
    ensure_data_dir()
    if LAST_BROADCAST_FILE.exists():
        with open(LAST_BROADCAST_FILE, "r") as f:
            data = json.load(f)
            return data.get("timestamp", 0)
    return 0


def save_last_broadcast(timestamp: float):
    """Save broadcast timestamp"""
    ensure_data_dir()
    with open(LAST_BROADCAST_FILE, "w") as f:
        json.dump({"timestamp": timestamp}, f)


def load_sent_news() -> set:
    """Load sent news titles"""
    ensure_data_dir()
    if SENT_NEWS_FILE.exists():
        with open(SENT_NEWS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_sent_news(sent_news: set):
    """Save sent news titles (keep only last 500)"""
    ensure_data_dir()
    news_list = list(sent_news)[-500:]
    with open(SENT_NEWS_FILE, "w") as f:
        json.dump(news_list, f)


def can_broadcast() -> bool:
    """Check if broadcast is allowed (1 hour since last broadcast)"""
    last_broadcast = load_last_broadcast()
    return time.time() - last_broadcast >= BROADCAST_INTERVAL_SECONDS


def get_time_until_next_broadcast() -> int:
    """Get seconds until next broadcast is allowed"""
    last_broadcast = load_last_broadcast()
    elapsed = time.time() - last_broadcast
    remaining = BROADCAST_INTERVAL_SECONDS - elapsed
    return max(0, int(remaining))


# ==================== Proxy Management ====================
def get_ssl_context():
    """Get SSL context with verification disabled"""
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


async def get_working_session() -> tuple[aiohttp.ClientSession, ssl.SSLContext]:
    """Get a working aiohttp session (tries proxy list)"""
    ssl_context = get_ssl_context()
    
    # Try each proxy
    for proxy_url in PROXY_LIST:
        try:
            connector = ProxyConnector.from_url(proxy_url, ssl=ssl_context)
            session = aiohttp.ClientSession(connector=connector)
            
            # Test connection
            async with session.get("https://cointelegraph.com/rss", 
                                   timeout=aiohttp.ClientTimeout(total=10),
                                   ssl=False) as resp:
                if resp.status == 200:
                    logger.info(f"Proxy connected: {proxy_url[:40]}...")
                    await session.close()
                    connector = ProxyConnector.from_url(proxy_url, ssl=ssl_context)
                    return aiohttp.ClientSession(connector=connector), ssl_context
            await session.close()
        except Exception as e:
            logger.warning(f"Proxy {proxy_url[:40]}... failed: {e}")
            try:
                await session.close()
            except:
                pass
    
    # If all proxies fail, try direct connection
    logger.warning("All proxies failed, trying direct connection...")
    return aiohttp.ClientSession(), ssl_context


# ==================== News Fetching ====================
def extract_image_from_entry(entry) -> str | None:
    """Extract image URL from RSS entry"""
    # Try media_content
    if hasattr(entry, 'media_content') and entry.media_content:
        for media in entry.media_content:
            if media.get('medium') == 'image' or media.get('type', '').startswith('image'):
                return media.get('url')
    
    # Try media_thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url')
    
    # Try enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image'):
                return enc.get('href') or enc.get('url')
    
    # Try extracting img tag from content or summary
    content = entry.get('content', [{}])[0].get('value', '') if entry.get('content') else ''
    summary = entry.get('summary', '')
    
    for text in [content, summary]:
        img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', text)
        if img_match:
            return img_match.group(1)
    
    return None


async def fetch_rss_feed(session: aiohttp.ClientSession, feed_config: dict) -> list:
    """Fetch news from a single RSS feed"""
    try:
        async with session.get(feed_config["url"], 
                               timeout=aiohttp.ClientTimeout(total=30),
                               ssl=False) as response:
            if response.status == 200:
                content = await response.text()
                feed = feedparser.parse(content)
                
                news_items = []
                for entry in feed.entries[:10]:
                    # Get full summary
                    summary = entry.get("summary", entry.get("description", ""))
                    clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
                    
                    # Extract image
                    image_url = extract_image_from_entry(entry)
                    
                    news_items.append({
                        "title": entry.get("title", ""),
                        "link": entry.get("link", ""),
                        "summary": clean_summary[:500],
                        "source": feed_config["name"],
                        "priority": feed_config["priority"],
                        "published": entry.get("published", ""),
                        "image": image_url,
                        "tags": [tag.get('term', '') for tag in entry.get('tags', [])][:3],
                    })
                logger.info(f"Fetched {feed_config['name']}: {len(news_items)} articles")
                return news_items
    except Exception as e:
        logger.error(f"Failed to fetch {feed_config['name']} RSS: {e}")
    return []


async def fetch_all_news() -> list:
    """Fetch news from all RSS sources"""
    all_news = []
    
    session, ssl_ctx = await get_working_session()
    try:
        tasks = [fetch_rss_feed(session, feed) for feed in RSS_FEEDS]
        results = await asyncio.gather(*tasks)
        
        for news_items in results:
            all_news.extend(news_items)
    finally:
        await session.close()
    
    # Sort by priority
    all_news.sort(key=lambda x: x["priority"])
    return all_news


def get_latest_unsent_news(all_news: list, sent_news: set) -> dict | None:
    """Get the latest unsent news article"""
    for news in all_news:
        if news["title"] not in sent_news:
            return news
    return None


# ==================== Message Formatting ====================
def format_news_message(news: dict) -> str:
    """Format news message with rich formatting"""
    title = news['title']
    summary = news['summary']
    source = news['source']
    link = news['link']
    tags = news.get('tags', [])
    
    # Truncate summary
    if len(summary) > 350:
        summary = summary[:350] + "..."
    
    # Build tags string
    tags_str = ""
    if tags:
        tags_str = " ".join([f"#{tag.replace(' ', '')}" for tag in tags if tag])
        if tags_str:
            tags_str = f"\n\n🏷️ {tags_str}"
    
    # Use HTML format
    message = (
        f"📰 <b>{escape_html(title)}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📝 {escape_html(summary)}\n"
        f"{tags_str}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"🔗 <a href='{link}'>Read Full Article</a>\n"
        f"📡 Source: <b>{source}</b>\n\n"
        f"💹 <i>Trade the latest crypto trends on SuperEx!</i>"
    )
    return message


def escape_html(text: str) -> str:
    """Escape HTML special characters"""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def get_trade_keyboard() -> InlineKeyboardMarkup:
    """Get Trade Now button keyboard"""
    keyboard = [
        [InlineKeyboardButton("🚀 Trade Now on SuperEx", url=TRADE_URL)],
        [InlineKeyboardButton("📱 Download App", url="https://www.superex.com/download")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================== Auto Registration ====================
async def track_chat_member(update: ChatMemberUpdated, context: ContextTypes.DEFAULT_TYPE):
    """Track when bot is added to or removed from a group"""
    result = update.chat_member
    chat = update.chat
    
    # Check if this is about the bot itself
    if result.new_chat_member.user.id != context.bot.id:
        return
    
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    
    # Bot was added to group
    if old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED] and \
       new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR]:
        groups = load_groups()
        groups.add(chat.id)
        save_groups(groups)
        logger.info(f"Bot added to group: {chat.id} ({chat.title}) - Auto registered")
        
        # Send welcome message
        try:
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "✅ <b>SuperEx News Bot Activated!</b>\n\n"
                    "� I will automatically broadcast the latest crypto news to this group.\n"
                    "⏰ Maximum one news per hour.\n\n"
                    "📊 <b>Admin Commands:</b>\n"
                    "• /status - View bot status\n"
                    "• /news - Get latest news manually\n"
                    "• /stop - Stop receiving news\n\n"
                    "💡 <i>Only group admins can use these commands</i>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to send welcome message: {e}")
    
    # Bot was removed from group
    elif old_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR] and \
         new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.BANNED]:
        groups = load_groups()
        groups.discard(chat.id)
        save_groups(groups)
        logger.info(f"Bot removed from group: {chat.id} ({chat.title}) - Unregistered")


# ==================== Bot Command Handlers ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - Admin only"""
    if not await admin_required(update, context):
        return
    
    chat = update.effective_chat
    
    if chat.type in ["group", "supergroup"]:
        groups = load_groups()
        groups.add(chat.id)
        save_groups(groups)
        
        await update.message.reply_text(
            "✅ <b>SuperEx News Bot Activated!</b>\n\n"
            "🔔 I will automatically broadcast the latest crypto news to this group.\n"
            "⏰ Maximum one news per hour.\n\n"
            "📊 <b>Admin Commands:</b>\n"
            "• /status - View bot status\n"
            "• /news - Get latest news manually\n"
            "• /stop - Stop receiving news\n\n"
            "💡 <i>Only group admins can use these commands</i>",
            parse_mode="HTML"
        )
        logger.info(f"Group registered: {chat.id} ({chat.title})")
    else:
        await update.message.reply_text(
            "👋 <b>Welcome to SuperEx News Bot!</b>\n\n"
            "Add me to a group and I will automatically broadcast crypto news.\n\n"
            "🔗 Just add me to your group - no setup needed!",
            parse_mode="HTML"
        )


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stop command - Admin only"""
    if not await admin_required(update, context):
        return
    
    chat = update.effective_chat
    
    if chat.type in ["group", "supergroup"]:
        groups = load_groups()
        groups.discard(chat.id)
        save_groups(groups)
        
        await update.message.reply_text(
            "🛑 <b>News broadcast stopped</b>\n\n"
            "Send /start to reactivate",
            parse_mode="HTML"
        )
        logger.info(f"Group unregistered: {chat.id}")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command - Admin only"""
    if not await admin_required(update, context):
        return
    
    groups = load_groups()
    chat = update.effective_chat
    
    is_registered = chat.id in groups if chat.type in ["group", "supergroup"] else False
    time_until_next = get_time_until_next_broadcast()
    
    minutes = time_until_next // 60
    seconds = time_until_next % 60
    
    status_text = (
        f"📊 <b>SuperEx News Bot Status</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📡 Registered Groups: <b>{len(groups)}</b>\n"
        f"⏰ Broadcast Interval: <b>1 hour</b>\n"
        f"⏳ Next Broadcast In: <b>{minutes}m {seconds}s</b>\n"
        f"✅ This Group: <b>{'Registered' if is_registered else 'Not Registered'}</b>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    await update.message.reply_text(status_text, parse_mode="HTML")


async def news_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /news command - Admin only"""
    if not await admin_required(update, context):
        return
    
    msg = await update.message.reply_text("🔄 Fetching latest news...")
    
    all_news = await fetch_all_news()
    
    if all_news:
        news = all_news[0]
        message = format_news_message(news)
        keyboard = get_trade_keyboard()
        
        # Try sending with image first
        if news.get('image'):
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=news['image'],
                    caption=message,
                    parse_mode="HTML",
                    reply_markup=keyboard
                )
                await msg.delete()
                return
            except Exception as e:
                logger.warning(f"Failed to send image: {e}")
        
        # Send text only
        await msg.edit_text(
            message,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False
        )
    else:
        await msg.edit_text("❌ Unable to fetch news. Please try again later.")


# ==================== Auto Broadcast ====================
async def broadcast_news(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled news broadcast to all groups"""
    if not can_broadcast():
        logger.info(f"Broadcast cooldown: {get_time_until_next_broadcast()} seconds remaining")
        return
    
    groups = load_groups()
    if not groups:
        logger.info("No registered groups")
        return
    
    # Fetch news
    all_news = await fetch_all_news()
    sent_news = load_sent_news()
    
    news = get_latest_unsent_news(all_news, sent_news)
    if not news:
        logger.info("No new unsent news")
        return
    
    # Format message
    message = format_news_message(news)
    keyboard = get_trade_keyboard()
    
    # Broadcast to all groups
    success_count = 0
    failed_groups = []
    
    for group_id in groups:
        try:
            # Try sending with image
            if news.get('image'):
                try:
                    await context.bot.send_photo(
                        chat_id=group_id,
                        photo=news['image'],
                        caption=message,
                        parse_mode="HTML",
                        reply_markup=keyboard
                    )
                    success_count += 1
                    logger.info(f"Broadcast to group (with image): {group_id}")
                    continue
                except Exception as img_error:
                    logger.warning(f"Image send failed, falling back to text: {img_error}")
            
            # Send text message
            await context.bot.send_message(
                chat_id=group_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=False
            )
            success_count += 1
            logger.info(f"Broadcast to group: {group_id}")
        except Exception as e:
            logger.error(f"Failed to broadcast to group {group_id}: {e}")
            failed_groups.append(group_id)
    
    # Remove failed groups (may have been kicked)
    if failed_groups:
        for group_id in failed_groups:
            groups.discard(group_id)
        save_groups(groups)
    
    # Record sent news
    sent_news.add(news["title"])
    save_sent_news(sent_news)
    
    # Update broadcast time
    save_last_broadcast(time.time())
    
    logger.info(f"Broadcast complete: {success_count}/{len(groups) + len(failed_groups)} groups")


# ==================== Main ====================
def main():
    """Start the bot"""
    logger.info("Starting SuperEx News Bot...")
    
    # Ensure data directory exists
    ensure_data_dir()
    
    # Create Application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stop", stop_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("news", news_command))
    
    # Add chat member handler for auto-registration
    application.add_handler(ChatMemberHandler(track_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    # Set up scheduled broadcast - check every 5 minutes
    job_queue = application.job_queue
    job_queue.run_repeating(broadcast_news, interval=300, first=10)
    
    logger.info("Bot started successfully!")
    
    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
