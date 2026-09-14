from loguru import logger


def calculate_image_consistency_fallback(embedding_df) -> float:
    try:
        if len(embedding_df) < 2:
            return 1.0

        import numpy as np
        from sklearn.metrics.pairwise import cosine_similarity

        embeddings = []
        for _, row in embedding_df.iterrows():
            embedding = row.get("embedding")
            if embedding is not None:
                if not isinstance(embedding, np.ndarray):
                    embedding = np.array(embedding)
                embeddings.append(embedding)

        if len(embeddings) < 2:
            return 1.0

        similarities = []
        for i in range(len(embeddings)):
            for j in range(i + 1, len(embeddings)):
                sim = cosine_similarity([embeddings[i]], [embeddings[j]])[0][0]
                similarities.append(sim)

        avg_similarity = float(np.mean(similarities))
        return max(0.0, min(1.0, avg_similarity))

    except Exception as exc:
        logger.warning(
            "[DecisionAnalyzer] Failed to calculate image consistency (fallback): {}",
            exc,
        )
        return 0.5