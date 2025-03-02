# LLM Prompt Visualization

A React-based visualization tool for viewing LLM prompts and conversations from log files.

## Features

- Real-time log file monitoring
- Collapsible conversation view
- Syntax highlighting for code blocks
- Metadata display
- Configurable log file source
- Configurable server port

## Setup

1. Install dependencies:
```bash
npm install
```

2. Configure the port (optional):
   - Create or modify `.env` file in the project root
   - Set `PORT` for the development server
   - Set `REACT_APP_DEFAULT_LOG_PORT` for the default log file URL
```bash
PORT=3000
REACT_APP_DEFAULT_LOG_PORT=3000
```

3. Start the development server:
```bash
npm start
```

4. Open [http://localhost:3000](http://localhost:3000) (or your configured port) to view the application

## Usage

1. The application will attempt to load logs from `http://localhost:<PORT>/log.json` by default (PORT is configured in .env)
2. You can change the log file URL using the text field at the top of the page
3. The left sidebar shows all conversations with their metadata
4. Click on a conversation to view its details in the main panel
5. Messages can be collapsed/expanded using the arrow icons

## Environment Variables

- `PORT`: The port on which the development server runs (default: 3000)
- `REACT_APP_DEFAULT_LOG_PORT`: The default port used in the log file URL (default: 3000)

## Log File Format

The log file should be a JSON array of objects with the following structure:

```json
[
  {
    "meta": {
      "type": "string",
      "name": "string",
      "is_retry": boolean
    },
    "messages": [
      {
        "role": "system|assistant|user",
        "content": "string"
      }
    ]
  }
]
``` 