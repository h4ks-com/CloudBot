# CloudBot Architecture Documentation

## Overview

CloudBot is a Python-based IRC bot framework designed to be simple, fast, and highly expandable. Built with asynchronous programming and plugin-based architecture, it provides a powerful foundation for IRC bot functionality.

## Core Architecture

### 1. Bot Structure

**Main Entry Point**: `cloudbot/__main__.py`
- Initializes CloudBot instance from `cloudbot/bot.py`
- Handles graceful shutdown with signal handling
- Supports restart functionality
- Manages logging configuration

**Core Bot (`cloudbot/bot.py`)**
- `CloudBot` class: Main bot instance
- `BotInstanceHolder`: Singleton pattern for global bot access
- Manages connections, plugins, and configuration
- Handles database initialization and connection pooling
- Coordinates plugin loading and hook execution

### 2. Configuration System

**Configuration Files**:
- `config.default.json`: Default configuration template
- `config.json`: Active configuration (copied from default)
- `pyproject.toml`: Project dependencies and tool configuration

**Configuration Structure**:
```json
{
  "connections": [...],        // IRC server connections
  "plugins": {...},           // Plugin-specific settings
  "api_keys": {...},          // External service API keys
  "database": "...",          // Database connection string
  "plugin_loading": {...},    // Plugin whitelist/blacklist
  "permissions": {...},       // User/group permissions
  "logging": {...}            // Logging configuration
}
```

**Connection Configuration**:
- Multiple IRC networks supported
- SSL/TLS support with certificate validation
- SASL authentication
- Rate limiting and flood protection
- Channel auto-join and permissions per network

### 3. Plugin System

**Plugin Directory**: `plugins/` (190+ plugins included)

**Plugin Architecture**:
- **Decorator-based hooks**: `@hook.command()`, `@hook.event()`, etc.
- **Automatic parameter injection**: Functions receive parameters based on signature
- **Hot reloading**: Plugins can be reloaded without bot restart
- **Isolated execution**: Each plugin runs in its own context

**Hook Types**:
- **Command hooks**: User-triggered commands (`.help`, `!weather`)
- **Event hooks**: IRC events (join, part, message, kick)
- **Regex hooks**: Pattern matching on messages
- **Periodic hooks**: Time-based execution
- **Lifecycle hooks**: Startup/shutdown events
- **Raw IRC hooks**: Direct IRC protocol handling

**Plugin Loading**:
- Configurable whitelist/blacklist system
- Dependency resolution and loading order
- Error isolation (failing plugins don't crash bot)
- Plugin metadata and hook registration

### 4. Event System

**Event Types** (`cloudbot/event.py`):
- `message`: Regular channel/private messages
- `action`: CTCP ACTION messages (/me)
- `notice`: NOTICE messages
- `join`: User joins channel
- `part`: User leaves channel
- `kick`: User kicked from channel
- `other`: Miscellaneous events

**Event Processing**:
- Event creation from IRC messages
- Hook matching and execution
- Parameter injection for hook functions
- Response handling and formatting
- Error handling and logging

### 5. Connection Management

**IRC Client** (`cloudbot/client.py`, `cloudbot/clients/irc.py`):
- Asynchronous IRC protocol implementation
- Connection state management
- Message parsing and formatting
- Automatic reconnection handling
- Ping/pong keepalive mechanism

**Multi-Network Support**:
- Multiple simultaneous IRC connections
- Per-connection configuration
- Independent plugin execution per network
- Shared database and global state

### 6. Database Integration

**Database Configuration**:
- SQLAlchemy ORM integration
- SQLite default (configurable for PostgreSQL/MySQL)
- Automatic table creation and migration
- Shared metadata across plugins

**Database Usage in Plugins**:
```python
from sqlalchemy import Column, String, Table
from cloudbot.util import database

table = Table(
    "plugin_data",
    database.metadata,
    Column("key", String),
    Column("value", String),
)

@hook.command("save")
def save_data(text, db):
    db.execute(table.insert().values(key="example", value=text))
    db.commit()
```

### 7. Permission System

**Permission Structure**:
- **Groups**: Named permission sets
- **Users**: IRC hostmasks assigned to groups
- **Permissions**: Named capabilities (`botcontrol`, `op`, etc.)

**Permission Checking**:
- Decorator-based: `@hook.command(permissions=["admin"])`
- Runtime checks: `event.has_permission("permission")`
- Hierarchical group membership

**Built-in Permissions**:
- `botcontrol`: Bot administration
- `permissions_users`: User/group management
- `op`: Channel operator functions
- `addfactoid`/`delfactoid`: Factoid management
- `ignore`: User ignore list management

### 8. Utility Framework

**Utility Modules** (`cloudbot/util/`):
- **async_util.py**: Async/await utilities
- **formatting.py**: Text formatting and IRC color codes
- **database.py**: Database connection and metadata
- **http.py**: HTTP client utilities
- **web.py**: Web scraping and parsing
- **timeformat.py**: Time parsing and formatting
- **pager.py**: Paginated output handling

### 9. Plugin API

**Available Parameters** (auto-injected):
- `text`: Command arguments or message content
- `nick`, `user`, `host`, `mask`: User identification
- `chan`: Channel name
- `conn`: Connection object
- `bot`: Bot instance
- `db`: Database session
- `event`: Full event object

**Response Functions**:
- `return "text"`: Send message to channel/user
- `reply(text)`: Reply with nickname prefix
- `notice(text)`: Send private notice
- `action(text)`: Send /me action
- `message(text, target)`: Send to specific target

**Hook Examples**:
```python
@hook.command("hello")
def hello_command(nick):
    return f"Hello {nick}!"

@hook.event([EventType.join])
def user_joined(nick, chan):
    return f"Welcome to {chan}, {nick}!"

@hook.regex(r"github\.com/([^/]+)/([^/\s]+)")
def github_link(match):
    user, repo = match.groups()
    return f"GitHub: {user}/{repo}"
```

## Configuration and Deployment

### 1. Installation Requirements

**Python Version**: 3.10+ (< 3.12)
**Key Dependencies**:
- `aiohttp`: Async HTTP client
- `sqlalchemy`: Database ORM
- `beautifulsoup4`: HTML parsing
- `watchdog`: File system monitoring
- IRC protocol libraries

### 2. Configuration Process

1. Copy `config.default.json` to `config.json`
2. Configure IRC connections and channels
3. Set API keys for external services
4. Configure permissions and user groups
5. Set plugin whitelist/blacklist if needed
6. Configure database connection (SQLite default)

### 3. Starting the Bot

```bash
uv sync                    # Install dependencies
uv run python -m cloudbot  # Start bot
```

### 4. Plugin Development

**Simple Plugin Example**:
```python
from cloudbot import hook

@hook.command("ping")
def ping_command():
    """- responds with pong"""
    return "pong!"
```

**Advanced Plugin with Database**:
```python
from sqlalchemy import Column, String, Table
from cloudbot import hook
from cloudbot.util import database

quotes_table = Table(
    "quotes",
    database.metadata,
    Column("quote", String),
    Column("author", String),
)

@hook.command("addquote")
def add_quote(text, nick, db):
    """<quote> - adds a quote"""
    db.execute(quotes_table.insert().values(quote=text, author=nick))
    db.commit()
    return "Quote added!"
```

## Security and Best Practices

### 1. Permission Model
- Principle of least privilege
- IRC hostmask-based authentication
- Group-based permission inheritance
- Per-network permission isolation

### 2. Input Validation
- SQL injection prevention via SQLAlchemy
- IRC message length limits
- Rate limiting and flood protection
- Input sanitization for external APIs

### 3. Error Handling
- Plugin isolation prevents cascading failures
- Graceful degradation for external services
- Comprehensive logging and debugging
- Automatic reconnection handling

### 4. Plugin Security
- No direct file system access by default
- API key management through configuration
- Sandboxed plugin execution
- Resource usage monitoring

## Extension Points

### 1. Custom Hook Types
- Implement new hook decorators
- Extend event system for custom events
- Add protocol support beyond IRC

### 2. Database Backends
- PostgreSQL/MySQL support
- Custom database adapters
- Migration system extensions

### 3. Protocol Extensions
- SASL mechanism plugins
- IRC capability extensions
- Custom IRC command handlers

### 4. Plugin Ecosystem
- Plugin dependency management
- Plugin repositories and distribution
- Plugin configuration schemas
- Runtime plugin installation

This architecture provides a robust, scalable foundation for IRC bot development with clear separation of concerns, extensive plugin capabilities, and production-ready reliability features.