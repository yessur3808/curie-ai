# memory/research.py

from datetime import datetime
from .database import mongo_db


class ResearchManager:
    @staticmethod
    def save_research(topic, content, user_internal_id=None):
        doc = {"topic": topic, "content": content, "timestamp": datetime.utcnow()}
        if user_internal_id is not None:
            doc["user_id"] = str(user_internal_id)
        mongo_db.research_memory.insert_one(doc)

    @staticmethod
    def search_research(topic, user_internal_id=None):
        query = {"topic": topic}
        if user_internal_id is not None:
            query["user_id"] = str(user_internal_id)
        results = mongo_db.research_memory.find(query).sort("timestamp", -1)
        return [doc["content"] for doc in results]

    @staticmethod
    def search_global_research(topic):
        return search_research(topic, user_internal_id=None)
