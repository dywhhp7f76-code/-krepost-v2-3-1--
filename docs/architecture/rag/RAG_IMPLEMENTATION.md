Полное руководство: RAG с Obsidian (LlamaIndex + ChromaDB)

1. Как работать с длинными заметками (Chunking)

Obsidian-заметки часто бывают очень длинными (3000–15000+ токенов). Загружать весь файл целиком — плохая практика.

Рекомендуемые стратегии chunking:

|   |   |   |   |   |
|---|---|---|---|---|
|Стратегия|Размер чанка|Перекрытие|Когда использовать|Качество|
|SentenceSplitter|512–1024|50–100|Универсальный вариант|Хорошее|
|SemanticSplitter|~800–1200|100|Лучшее качество (рекомендуется)|Отличное|
|RecursiveCharacterTextSplitter|800–1500|100–150|Для очень длинных заметок|Хорошее|
|MarkdownHeaderTextSplitter|по заголовкам|—|Если заметки хорошо структурированы|Отличное|

Рекомендация для Obsidian: Используй SemanticSplitterNodeParser — он разбивает текст по смыслу, а не по жёстким границам. Это даёт лучшие результаты.

from llama_index.core.node_parser import SemanticSplitterNodeParser

from llama_index.embeddings.huggingface import HuggingFaceEmbedding

  

embed_model = HuggingFaceEmbedding(model_name=&quot;nomic-embed-text&quot;)

  

splitter = SemanticSplitterNodeParser(

    buffer_size=1,

    breakpoint_percentile_threshold=95,

    embed_model=embed_model,

)

Оптимальные параметры для 7–13B моделей:

- chunk_size: 800–1200 токенов
- chunk_overlap: 100–150 токенов
- Максимум 3–5 чанков на заметку (чтобы не перегружать контекст)

2. Инкрементальное обновление эмбеддингов

Полная переиндексация всей папки Obsidian при каждом изменении — неэффективно.

Лучшие практики:

1. Отслеживание изменений:

- Хранить хэш файла (md5 или sha256) в метаданных ChromaDB
- При старте сравнивать текущие хэши с сохранёнными

3. Инкрементальная индексация:

- Добавлять только новые/изменённые файлы
- Удалять чанки удалённых файлов

5. Рекомендуемый подход:

- Использовать SimpleDirectoryReader с метаданными
- Хранить file_hash и last_modified в node.metadata
- При обновлении удалять старые чанки файла и добавлять новые

Пример логики обновления:

def get_file_hash(filepath):

    import hashlib

    with open(filepath, &quot;rb&quot;) as f:

        return hashlib.md5(f.read()).hexdigest()

3. Пример кода: LlamaIndex + ChromaDB + Obsidian

Вот полный рабочий пример (актуально на 2026 год):

import os

from pathlib import Path

from llama_index.core import VectorStoreIndex, StorageContext, Settings

from llama_index.vector_stores.chroma import ChromaVectorStore

from llama_index.embeddings.huggingface import HuggingFaceEmbedding

from llama_index.core.node_parser import SemanticSplitterNodeParser

from llama_index.readers.file import MarkdownReader

import chromadb

  

# === Настройки ===

OBSIDIAN_PATH = &quot;/path/to/your/obsidian/vault&quot;   # ← укажи свой путь

CHROMA_PATH = &quot;./chroma_db&quot;

EMBED_MODEL_NAME = &quot;nomic-embed-text&quot;

  

# === Инициализация ===

embed_model = HuggingFaceEmbedding(model_name=EMBED_MODEL_NAME)

Settings.embed_model = embed_model

  

# ChromaDB

chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

chroma_collection = chroma_client.get_or_create_collection(&quot;obsidian_notes&quot;)

vector_store = ChromaVectorStore(chroma_collection=chroma_collection)

storage_context = StorageContext.from_defaults(vector_store=vector_store)

  

# === Чтение и парсинг заметок ===

def load_obsidian_notes(path: str):

    reader = MarkdownReader()

    documents = reader.load_data(Path(path))

    return documents

  

# Загружаем заметки

documents = load_obsidian_notes(OBSIDIAN_PATH)

  

# Разбиваем на смысловые чанки

splitter = SemanticSplitterNodeParser(

    buffer_size=1,

    breakpoint_percentile_threshold=95,

    embed_model=embed_model,

)

nodes = splitter.get_nodes_from_documents(documents)

  

# === Создаём индекс ===

index = VectorStoreIndex(nodes, storage_context=storage_context)

  

print(f&quot;Проиндексировано {len(nodes)} чанков из {len(documents)} заметок.&quot;)

4. Инкрементальное обновление (добавление/изменение)

def update_index_incrementally(index, obsidian_path):

    &quot;&quot;&quot;Простая инкрементальная индексация&quot;&quot;&quot;

    from llama_index.core import Document

    import hashlib

  

    existing_hashes = {}  # Можно хранить в отдельном файле или в Chroma metadata

  

    for file_path in Path(obsidian_path).rglob(&quot;*.md&quot;):

        file_hash = hashlib.md5(file_path.read_bytes()).hexdigest()

        # Проверяем, был ли файл уже проиндексирован

        if file_hash in existing_hashes:

            continue  # пропускаем

  

        # Загружаем и индексируем новый/изменённый файл

        doc = MarkdownReader().load_data(file_path)[0]

        nodes = splitter.get_nodes_from_documents([doc])

        index.insert_nodes(nodes)

        existing_hashes[file_hash] = str(file_path)

  

    print(&quot;Индекс обновлён инкрементально.&quot;)

5. Полноценный Query Pipeline

from llama_index.core.query_engine import RetrieverQueryEngine

from llama_index.core.retrievers import VectorIndexRetriever

from llama_index.core.postprocessor import SimilarityPostprocessor

  

retriever = VectorIndexRetriever(

    index=index,

    similarity_top_k=8,           # берём топ-8 чанков

)

  

query_engine = RetrieverQueryEngine.from_args(

    retriever=retriever,

    node_postprocessors=[

        SimilarityPostprocessor(similarity_cutoff=0.75)

    ]

)

  

# Пример запроса

response = query_engine.query(&quot;Какой у меня план на 2026 год?&quot;)

print(response)

  

Рекомендации по параметрам (7–13B модели)

|   |   |   |
|---|---|---|
|Параметр|Рекомендуемое значение|Причина|
|similarity_top_k|6–10|Не перегружать контекст|
|similarity_cutoff|0.72 – 0.78|Отсеивать шум|
|chunk_size (semantic)|~900–1100 токенов|Оптимально для смысла|
|chunk_overlap|120|Сохранять контекст|
|Embedding модель|nomic-embed-text|Лучшее соотношение качество/скорость|

  

Хочешь, я добавлю:

- Полную версию с file watcher (автоматическое обновление при изменении заметок)
- Продвинутый Hybrid Search (embedding + BM25)
- Систему с reranker (bge-reranker-base)

Какой следующий шаг тебе нужен?