import os

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser

from graph.state import _SYSTEM_PROMPT

_llm = ChatOpenAI(
    base_url=os.getenv("LLM_BASE_URL", "https://llm.jetstream-cloud.org/llama-4-scout/v1/"),
    api_key=os.getenv("OPENROUTER_API_KEY", "none"),
    model=os.getenv("LLM_MODEL", "llama-4-scout"),
    temperature=0.3,
    max_tokens=1000,
)

# General Q&A — full conversation history with system prompt prepended
qa_chain = (
    ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        MessagesPlaceholder("messages"),
    ])
    | _llm
    | StrOutputParser()
)

# UniProt annotation summarizer → structured JSON {Structure, Function, Sequence}
annotation_chain = (
    ChatPromptTemplate.from_messages([
        ("system", "You're an expert assistant for structural biologists."),
        ("human",
         "Summarize the following protein annotations into three categories:\n"
         "- Structure (domains, motifs, folding)\n"
         "- Function (enzymatic activity, pathways)\n"
         "- Sequence features (PTMs, polymorphisms, isoforms, signal peptides)\n\n"
         "Return strictly as JSON with keys: \"Structure\", \"Function\", \"Sequence\". "
         "Each value is a list of bullet-point strings.\n\n---\n{annotations}\n---"),
    ])
    | _llm
    | JsonOutputParser()
)

# Literature Q&A — answer question against retrieved paper excerpt
literature_qa_chain = (
    ChatPromptTemplate.from_messages([
        ("system", "You are an expert research assistant for protein engineers and biochemists."),
        ("human",
         "Use the paper (DOI: {doi}) to answer the following question.\n"
         "The paper may describe the protein family or close homologs — use any relevant context "
         "about the protein family, catalytic mechanism, conserved residues, or structural class "
         "to give the best possible answer.\n\n"
         "Question: {question}\n"
         "---\nPaper Excerpt (first 10 000 chars):\n{paper_text}\n---\nAnswer:"),
    ])
    | _llm
    | StrOutputParser()
)

# Annotation fallback Q&A — used when no paper text is available
annotation_qa_chain = (
    ChatPromptTemplate.from_messages([
        ("system", "You are an expert research assistant for protein engineers and biochemists."),
        ("human",
         "{paper_note}"
         "Answer the following question using only the metadata and annotations provided below. "
         "Clearly note at the end that your answer is based on available annotations only.\n\n"
         "Question: {question}\n"
         "---\nAvailable context:\n{context}\n---\nAnswer:"),
    ])
    | _llm
    | StrOutputParser()
)
