# Jira Attachment MCP Server

An MCP server that lets Claude download and read Jira issue attachments directly via the Atlassian REST API.

## Tools Provided

| Tool | Description |
|------|-------------|
| `list_attachments` | List all attachments on a Jira issue |
| `download_attachment` | Download a specific attachment by filename |
| `download_all_attachments` | Download all attachments from an issue |
| `read_attachment_text` | Read text-based files (.csv, .sql, .json, etc.) directly without saving |
| `get_issue_attachments_summary` | Quick overview: count, total size, file types |

## Setup

### 1. Generate Atlassian API Token

Go to [Atlassian API Tokens](https://id.atlassian.com/manage-profile/security/api-tokens) and create a new token.

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Create Virtual Environment & Install Dependencies

```bash
cd mcpServers/jira-attachment-server
python -m venv .venv

# Activate (Windows)
.venv\Scripts\activate

# Activate (macOS/Linux)
# source .venv/bin/activate

pip install -r requirements.txt
```

### 4. Register with Claude Desktop / Cowork

Add to your Claude Desktop config (`claude_desktop_config.json`).
Point the `command` to the Python executable inside the venv so dependencies are always available.

Credentials are loaded from the `.env` file (Step 2), so no `env` block is needed in the config.

**Windows:**
```json
{
  "mcpServers": {
    "jira-attachments": {
      "command": "C:\\Users\\sabhy\\WK\\Star Agent\\mcpServers\\jira-attachment-server\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\sabhy\\WK\\Star Agent\\mcpServers\\jira-attachment-server\\server.py"]
    }
  }
}
```

**macOS/Linux:**
```json
{
  "mcpServers": {
    "jira-attachments": {
      "command": "/path/to/mcpServers/jira-attachment-server/.venv/bin/python",
      "args": ["/path/to/mcpServers/jira-attachment-server/server.py"]
    }
  }
}
```

> **Alternative:** If you prefer not to keep a `.env` file in the project, you can pass credentials via the `env` block in the config instead:
> ```json
> "env": {
>   "ATLASSIAN_EMAIL": "your-email@company.com",
>   "ATLASSIAN_API_TOKEN": "your-api-token",
>   "ATLASSIAN_BASE_URL": "https://yoursite.atlassian.net"
> }
> ```

## Usage Examples

Once connected, Claude can:

- **"List attachments on STAR-158773"** → calls `list_attachments`
- **"Download Blueprint_Health_Check_V1.0.xlsx from STAR-158773"** → calls `download_attachment`
- **"Read the SQL file attached to STAR-12345"** → calls `read_attachment_text`
- **"Download everything from STAR-158773"** → calls `download_all_attachments`
