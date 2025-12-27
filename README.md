# GitHub RAG Agent

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green)
![Status](https://img.shields.io/badge/status-active-success)

[简体中文 (Chinese Version)](README_CN.md)

## Overview

The **GitHub RAG Agent** is an autonomous code analysis system designed to facilitate rapid understanding of complex codebases. Leveraging **Google Gemini** models and **Retrieval-Augmented Generation (RAG)** technology, this system goes beyond simple chat interfaces by implementing a complete agentic workflow.

The agent autonomously perceives the repository structure, plans which critical files to analyze to minimize token usage, indexes the codebase into a vector database, and provides grounded answers to architectural and logical queries.

## Key Features

* **Agentic Planning**: Automatically fetches the repository file tree and utilizes an LLM planner to identify the top 3-5 critical files for analysis, ensuring efficient token consumption.
* **Retrieval-Augmented Generation (RAG)**: Integrates ChromaDB to generate and store vector embeddings of code segments, enabling high-precision context retrieval for user queries.
* **Real-time Event Streaming**: Utilizes Server-Sent Events (SSE) to provide real-time feedback on the agent's operational status, including connection, planning, downloading, and indexing stages.
* **Engineering-Grade Architecture**: Refactored from flat scripts into a modular, service-oriented architecture suitable for scalability and maintenance.

## System Architecture

The system follows a modular design separating the API layer, business logic, and data storage.

[![](https://mermaid.ink/img/pako:eNp1UtuOmzAQ_RXLT62UpTHkUniolIUku9JWijZRK5XsgwOTxBLYyJjtpiH_3jGXNo26lmAuzJkzc_CZJioFGtCD5sWRbKKtJHjCTIA08XfYde4Lubv7Uj9sNqtP6_W8JrPVY7zgpUFL5tLo00sLtLGtjERZcJMca7IG_SoSiGcHbNNHXXX7Lqtdy76lodLQ15Rb2n63p8s1vZlDFoC9yUYD1GQpzEO1i1tzQ3ALdR0MM0gMWYgMypqsMi4l6Pjp6WvvvwP1HDLPd5CSEAWryTdsonR0H7fODS_I9J0NH-Veo266Skyl4XrFbgHL1bkoZk2eIVcGnqFQcesS65cCSU9Xo_bzNPhm0FTIA24YHrXKefyhtdH9xytQt3GDwcFAg0ysopALKeKlUocMusgO8__l_mEOlTTwZnBGowW88gwXmC1jfDBVFkqmfwS2OQuZyfIn6Lq7aHSAV1GkNECJYEBz0Dm3IT1b1JaaI-QoW4BuCnteZcZqeEFYweUPpfIeqVV1ONJgz7MSo6pIuYFIcPwPf0vAjhOqCmkDlw2bHjQ40zcajIfOkLnjiTvx2efp2J2OBvREAzZmznTk-5gcsaHrscuA_mpYmcN85k89f-JPPOZ6k9HlN_ZsD98?type=png)](https://mermaid.live/edit#pako:eNp1UtuOmzAQ_RXLT62UpTHkUniolIUku9JWijZRK5XsgwOTxBLYyJjtpiH_3jGXNo26lmAuzJkzc_CZJioFGtCD5sWRbKKtJHjCTIA08XfYde4Lubv7Uj9sNqtP6_W8JrPVY7zgpUFL5tLo00sLtLGtjERZcJMca7IG_SoSiGcHbNNHXXX7Lqtdy76lodLQ15Rb2n63p8s1vZlDFoC9yUYD1GQpzEO1i1tzQ3ALdR0MM0gMWYgMypqsMi4l6Pjp6WvvvwP1HDLPd5CSEAWryTdsonR0H7fODS_I9J0NH-Veo266Skyl4XrFbgHL1bkoZk2eIVcGnqFQcesS65cCSU9Xo_bzNPhm0FTIA24YHrXKefyhtdH9xytQt3GDwcFAg0ysopALKeKlUocMusgO8__l_mEOlTTwZnBGowW88gwXmC1jfDBVFkqmfwS2OQuZyfIn6Lq7aHSAV1GkNECJYEBz0Dm3IT1b1JaaI-QoW4BuCnteZcZqeEFYweUPpfIeqVV1ONJgz7MSo6pIuYFIcPwPf0vAjhOqCmkDlw2bHjQ40zcajIfOkLnjiTvx2efp2J2OBvREAzZmznTk-5gcsaHrscuA_mpYmcN85k89f-JPPOZ6k9HlN_ZsD98)

## Project Structure

The project adopts a domain-driven package structure:

Plaintext

```
github-rag-agent/
├── app/
│   ├── core/
│   │   └── config.py          # Centralized configuration management
│   ├── services/
│   │   ├── agent_service.py   # Core orchestration logic
│   │   ├── github_service.py  # GitHub API interaction adapter
│   │   └── vector_service.py  # Vector database management (ChromaDB)
│   ├── utils/
│   │   └── llm_client.py      # LLM client wrapper
│   └── main.py                # Application entry point and router
├── frontend/
│   └── index.html             # Client-side interface
├── .env                       # Environment variables
├── requirements.txt           # Project dependencies
└── README.md                  # Documentation
```

## Installation and Usage

### Prerequisites

- Python 3.9 or higher
- Google Gemini API Key
- GitHub Access Token (Classic)

### Setup

1. **Clone the repository**

   Bash

   ```
   git clone [https://github.com/your-username/github-rag-agent.git](https://github.com/your-username/github-rag-agent.git)
   cd github-rag-agent
   ```

2. **Install dependencies**

   Bash

   ```
   pip install -r requirements.txt
   ```

3. **Environment Configuration** Create a `.env` file in the root directory:

   Ini, TOML

   ```
   GEMINI_API_KEY=your_api_key
   GITHUB_TOKEN=your_github_token
   ```

### Running the Application

1. **Start the Backend** Use the module execution method to ensure proper package resolution:

   Bash

   ```
   python -m app.main
   ```

   The server will start at `http://127.0.0.1:8000`.

2. **Access the Client** Open `frontend/index.html` in a modern web browser.

## API Endpoints

- `GET /analyze?url={repo_url}`: Initiates the SSE stream for repository analysis.
- `POST /chat`: Accepts a JSON payload `{"query": "..."}` and returns a RAG-based response with source citations.
- `GET /`: Health check endpoint.

## Future Optimization Roadmap

### 1. Vector Storage Persistence

- **Current State**: ChromaDB runs in ephemeral (in-memory) mode. Data is lost upon server restart.
- **Optimization**: Configure ChromaDB to use persistent disk storage or integrate with a cloud-native vector database (e.g., Pinecone, Milvus) to cache analyzed repositories.

### 2. Advanced Chunking Strategies

- **Current State**: Basic file-level truncation or character splitting.
- **Optimization**: Implement AST-based (Abstract Syntax Tree) chunking for Python/JS code to ensure vector chunks respect function and class boundaries, improving retrieval accuracy.

### 3. Session Management

- **Current State**: Single global vector store instance.
- **Optimization**: Implement session-based isolation to allow multiple users to analyze different repositories simultaneously without data cross-contamination.

### 4. LLM Flexibility

- **Current State**: Tightly coupled with Google Gemini SDK.
- **Optimization**: Abstract the LLM interface (using LangChain or a custom adapter pattern) to support OpenAI, Anthropic, or local LLMs (via Ollama).

## License

This project is licensed under the MIT License.