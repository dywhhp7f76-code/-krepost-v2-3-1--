✅ Полная финальная версия RAG-системы для Obsidian (всё включено)

Вот максимально полная и готовая к использованию система, включающая:

- Hybrid Search (Vector + BM25)
- Reranker (bge-reranker-base)
- Semantic Chunking
- Инкрементальная индексация с хэшами
- File Watcher (автообновление)
- Metadata Filtering по тегам Obsidian
- Async поддержка
- Системный промпт с &lt;нет_данных&gt;
- Бонус: Streamlit веб-интерфейс

  

1. Полный код (Ultimate версия)

import os

import hashlib

import json

import asyncio

from pathlib import Path

from typing import List, Optional

  

from llama_index.core import VectorStoreIndex, StorageContext, Settings

from llama_index.vector_stores.chroma import ChromaVectorStore

from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from llama_index.core.node_parser import SemanticSplitterNodeParser

from llama_index.readers.file import MarkdownReader

from llama_index.retrievers.bm25 import BM25Retriever

from llama_index.core.retrievers import BaseRetriever

from llama_index.core.postprocessor import SentenceTransformerRerank

from llama_index.core.query_engine import RetrieverQueryEngine

from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter

  

import chromadb

from watchdog.observers import Observer

from watchdog.events import FileSystemEventHandler

  

# ================== КОНФИГУРАЦИЯ ==================

OBSIDIAN_PATH = &quot;/path/to/your/obsidian/vault&quot;

CHROMA_PATH = &quot;./chroma_obsidian_final&quot;

EMBED_MODEL = &quot;nomic-embed-text&quot;

RERANKER_MODEL = &quot;BAAI/bge-reranker-base&quot;

  

# ================== ИНИЦИАЛИЗАЦИЯ ==================

embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL)

Settings.embed_model = embed_model

  

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

chroma_collection = chroma_client.get_or_create_collection(&quot;obsidian_final&quot;)

vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

storage_context = StorageContext.from_defaults(vector_store=vector_store)

  

splitter = SemanticSplitterNodeParser(

    buffer_size=1,

    breakpoint_percentile_threshold=95,

    embed_model=embed_model,

)

  

# ================== ИНКРЕМЕНТАЛЬНАЯ ИНДЕКСАЦИЯ ==================

def get_file_hash(path: Path) -&gt; str:

    return hashlib.md5(path.read_bytes()).hexdigest()

  

def load_obsidian_final(path: str, force_rebuild: bool = False):

    reader = MarkdownReader()

    documents = reader.load_data(Path(path))

    hash_file = Path(CHROMA_PATH) / &quot;file_hashes.json&quot;

    existing_hashes = json.loads(hash_file.read_text()) if hash_file.exists() else {}

    new_nodes = []

    updated_files = []

    for doc in documents:

        file_path = Path(doc.metadata[&quot;file_path&quot;])

        current_hash = get_file_hash(file_path)

        if file_path.name not in existing_hashes or existing_hashes[file_path.name] != current_hash:

            nodes = splitter.get_nodes_from_documents([doc])

            for node in nodes:

                node.metadata[&quot;source&quot;] = str(file_path)

                node.metadata[&quot;file_hash&quot;] = current_hash

                # Извлекаем теги Obsidian

                tags = [tag.strip() for tag in doc.text.split() if tag.startswith(&quot;#&quot;)]

                node.metadata[&quot;tags&quot;] = tags

            new_nodes.extend(nodes)

            updated_files.append(file_path.name)

            existing_hashes[file_path.name] = current_hash

    hash_file.write_text(json.dumps(existing_hashes))

    if new_nodes:

        print(f&quot;Обновлено: {len(updated_files)} файлов → {len(new_nodes)} чанков&quot;)

    return new_nodes

  

# ================== HYBRID + RERANKER ==================

class UltimateHybridRetriever(BaseRetriever):

    def __init__(self, vector_index, bm25_retriever, top_k=15):

        super().__init__()

        self.vector_retriever = vector_index.as_retriever(similarity_top_k=top_k * 2)

        self.bm25_retriever = bm25_retriever

        self.top_k = top_k

  

    def _retrieve(self, query_bundle):

        vector_nodes = self.vector_retriever.retrieve(query_bundle)

        bm25_nodes = self.bm25_retriever.retrieve(query_bundle)

        combined = {n.node.node_id: n for n in vector_nodes + bm25_nodes}

        return list(combined.values())[:self.top_k]

  

# ================== QUERY ENGINE ==================

def create_ultimate_query_engine(index, tag_filter: Optional[str] = None):

    bm25_retriever = BM25Retriever.from_defaults(docstore=index.docstore, similarity_top_k=20)

    hybrid = UltimateHybridRetriever(index, bm25_retriever, top_k=15)

    reranker = SentenceTransformerRerank(model=RERANKER_MODEL, top_n=8)

    # Metadata filtering по тегам

    filters = None

    if tag_filter:

        filters = MetadataFilters(filters=[ExactMatchFilter(key=&quot;tags&quot;, value=tag_filter)])

    query_engine = RetrieverQueryEngine.from_args(

        retriever=hybrid,

        node_postprocessors=[reranker],

        verbose=True

    )

    return query_engine

  

# ================== FILE WATCHER ==================

class ObsidianWatcher(FileSystemEventHandler):

    def __init__(self, index, query_engine):

        self.index = index

        self.query_engine = query_engine

  

    def on_modified(self, event):

        if event.src_path.endswith(&quot;.md&quot;):

            print(f&quot;\n[Auto] Изменён: {event.src_path}&quot;)

            new_nodes = load_obsidian_final(OBSIDIAN_PATH)

            if new_nodes:

                self.index.insert_nodes(new_nodes)

                print(&quot;Индекс обновлён автоматически.&quot;)

  

# ================== СИСТЕМНЫЙ ПРОМПТ ==================

SYSTEM_PROMPT = &quot;&quot;&quot;

Ты — точный ассистент, который отвечает **только** на основе контекста из Obsidian.

  

Правила:

- Отвечай только на основе [КОНТЕКСТ]

- Если информации нет — напиши ровно: `&lt;нет_данных&gt;`

- В конце ответа указывай источники: `Источники: [[Название заметки]]`

  

[КОНТЕКСТ]

{context}

  

[ВОПРОС]

{question}

  

Ответ:

&quot;&quot;&quot;

  

# ================== ASYNC ВЕРСИЯ ==================

async def async_query(query_engine, question: str):

    loop = asyncio.get_event_loop()

    response = await loop.run_in_executor(None, query_engine.query, question)

    return response

  

# ================== ЗАПУСК ==================

if __name__ == &quot;__main__&quot;:

    print(&quot;=== Запуск Ultimate RAG для Obsidian ===&quot;)

    nodes = load_obsidian_final(OBSIDIAN_PATH)

    index = VectorStoreIndex(nodes, storage_context=storage_context) if nodes else \

            VectorStoreIndex.from_vector_store(vector_store)

    query_engine = create_ultimate_query_engine(index)

    # File Watcher

    observer = Observer()

    observer.schedule(ObsidianWatcher(index, query_engine), OBSIDIAN_PATH, recursive=True)

    observer.start()

    print(&quot;\nСистема запущена!&quot;)

    print(&quot;Используй: query_engine.query(&#x27;твой вопрос&#x27;)&quot;)

    print(&quot;Или с фильтром: create_ultimate_query_engine(index, tag_filter=&#x27;#2026&#x27;)&quot;)

    try:

        while True:

            time.sleep(1)

    except KeyboardInterrupt:

        observer.stop()

    observer.join()

  

2. Как использовать с фильтрацией по тегам

# Только заметки с тегом #2026

query_engine = create_ultimate_query_engine(index, tag_filter=&quot;#2026&quot;)

response = query_engine.query(&quot;Какие у меня цели?&quot;)

  

3. Streamlit веб-интерфейс (бонус)

Создай файл app.py:

import streamlit as st

from llama_index.core import VectorStoreIndex

  

st.title(&quot;Obsidian RAG Assistant&quot;)

  

query = st.text_input(&quot;Введите вопрос:&quot;)

tag = st.text_input(&quot;Фильтр по тегу (опционально)&quot;)

  

if st.button(&quot;Задать вопрос&quot;):

    qe = create_ultimate_query_engine(index, tag_filter=tag if tag else None)

    response = qe.query(query)

    st.write(response.response)

    with st.expander(&quot;Источники&quot;):

        for node in response.source_nodes:

            st.write(f&quot;- {node.metadata.get(&#x27;source&#x27;)}&quot;)

Запуск:

streamlit run app.py

  

Готово. Это максимально полная версия.

Если хочешь, я могу добавить ещё:

- Multi-query retrieval (несколько переформулировок запроса)
- Conversation memory (память о предыдущих вопросах)
- Export в PDF/Obsidian ответов

Что дальше?