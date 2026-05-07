from graph.state import PEATState
from graph.chains import qa_chain


def llm_qa(state: PEATState) -> dict:
    try:
        answer = qa_chain.invoke({"messages": state["messages"]})
    except Exception as e:
        answer = f"LLM error: {e}"
    return {"response_text": answer}
