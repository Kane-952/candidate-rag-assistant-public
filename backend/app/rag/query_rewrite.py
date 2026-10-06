def rewrite_query(history, current_question, llm):
    """历史只用于改写；pipeline 不把历史传给 gate 或 generation。"""
    if not history:
        return current_question
    return llm.rewrite_query(history, current_question)
