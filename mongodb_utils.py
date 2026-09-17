"""
MongoDB Database Utilities for Quiz Bot
Replaces SQLite with MongoDB for better scalability
"""

import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from motor.motor_asyncio import AsyncClient, AsyncDatabase, AsyncCollection
from pymongo import ASCENDING, DESCENDING
from dotenv import load_dotenv

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "quiz_bot_db")

logger = logging.getLogger(__name__)


class MongoDBManager:
    """Async MongoDB Manager for Quiz Bot"""
    
    def __init__(self):
        self.client: Optional[AsyncClient] = None
        self.db: Optional[AsyncDatabase] = None
        self.quizzes: Optional[AsyncCollection] = None
        self.questions: Optional[AsyncCollection] = None
        self.broadcast_users: Optional[AsyncCollection] = None
        self.broadcast_groups: Optional[AsyncCollection] = None
        self.autoruns: Optional[AsyncCollection] = None
    
    async def connect(self):
        """Connect to MongoDB"""
        try:
            self.client = AsyncClient(MONGODB_URI)
            self.db = self.client[MONGODB_DB_NAME]
            
            # Initialize collections
            self.quizzes = self.db["quizzes"]
            self.questions = self.db["questions"]
            self.broadcast_users = self.db["broadcast_users"]
            self.broadcast_groups = self.db["broadcast_groups"]
            self.autoruns = self.db["autoruns"]
            
            # Create indexes
            await self._create_indexes()
            
            # Verify connection
            await self.client.admin.command('ping')
            logger.info("✅ Connected to MongoDB successfully")
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            raise
    
    async def close(self):
        """Close MongoDB connection"""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")
    
    async def _create_indexes(self):
        """Create necessary indexes for better performance"""
        try:
            # Quizzes indexes
            await self.quizzes.create_index([("creator_id", ASCENDING)])
            await self.quizzes.create_index([("quiz_id", ASCENDING)], unique=True)
            
            # Questions indexes
            await self.questions.create_index([("quiz_id", ASCENDING)])
            
            # Broadcast indexes
            await self.broadcast_users.create_index([("chat_id", ASCENDING)], unique=True)
            await self.broadcast_groups.create_index([("chat_id", ASCENDING)], unique=True)
            
            # Autorun indexes
            await self.autoruns.create_index([("quiz_id", ASCENDING)])
            await self.autoruns.create_index([("active", ASCENDING)])
            
            logger.info("✅ Database indexes created")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")
    
    # ========== QUIZ OPERATIONS ==========
    
    async def create_quiz(self, creator_id: int, title: str, description: str, 
                         timer: int = 30, negative_value: float = 0.0) -> int:
        """Create a new quiz and return its ID"""
        try:
            # Get next quiz_id
            last_quiz = await self.quizzes.find_one(sort=[("quiz_id", DESCENDING)])
            quiz_id = (last_quiz["quiz_id"] + 1) if last_quiz else 1
            
            quiz_doc = {
                "quiz_id": quiz_id,
                "creator_id": creator_id,
                "title": title,
                "description": description,
                "timer": timer,
                "negative_value": negative_value,
                "created_at": datetime.utcnow()
            }
            
            await self.quizzes.insert_one(quiz_doc)
            logger.info(f"✅ Quiz created: ID={quiz_id}, Title='{title}'")
            return quiz_id
        except Exception as e:
            logger.error(f"❌ Error creating quiz: {e}")
            raise
    
    async def get_quiz(self, quiz_id: int) -> Optional[Dict]:
        """Get quiz by ID"""
        try:
            return await self.quizzes.find_one({"quiz_id": quiz_id})
        except Exception as e:
            logger.error(f"❌ Error getting quiz: {e}")
            return None
    
    async def update_quiz(self, quiz_id: int, **kwargs) -> bool:
        """Update quiz fields"""
        try:
            result = await self.quizzes.update_one(
                {"quiz_id": quiz_id},
                {"$set": kwargs}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error updating quiz: {e}")
            return False
    
    async def get_user_quizzes(self, creator_id: int, limit: int = 50) -> List[Dict]:
        """Get all quizzes created by a user"""
        try:
            cursor = self.quizzes.find({"creator_id": creator_id}).sort("quiz_id", DESCENDING).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            logger.error(f"❌ Error getting user quizzes: {e}")
            return []
    
    # ========== QUESTION OPERATIONS ==========
    
    async def add_question(self, quiz_id: int, question_text: str, options: List[str],
                          correct_answer: int, explanation: str = "", 
                          pre_message: str = "") -> Optional[int]:
        """Add a question to a quiz"""
        try:
            # Get next question id
            last_q = await self.questions.find_one(sort=[("id", DESCENDING)])
            q_id = (last_q["id"] + 1) if last_q else 1
            
            question_doc = {
                "id": q_id,
                "quiz_id": quiz_id,
                "question_text": question_text,
                "options": options,
                "correct_answer": correct_answer,
                "explanation": explanation,
                "pre_message": pre_message,
                "created_at": datetime.utcnow()
            }
            
            await self.questions.insert_one(question_doc)
            logger.info(f"✅ Question added: ID={q_id}, Quiz={quiz_id}")
            return q_id
        except Exception as e:
            logger.error(f"❌ Error adding question: {e}")
            return None
    
    async def get_quiz_questions(self, quiz_id: int) -> List[Dict]:
        """Get all questions for a quiz"""
        try:
            cursor = self.questions.find({"quiz_id": quiz_id})
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"❌ Error getting questions: {e}")
            return []
    
    async def get_question(self, question_id: int, quiz_id: int) -> Optional[Dict]:
        """Get a specific question"""
        try:
            return await self.questions.find_one({"id": question_id, "quiz_id": quiz_id})
        except Exception as e:
            logger.error(f"❌ Error getting question: {e}")
            return None
    
    async def update_question(self, question_id: int, **kwargs) -> bool:
        """Update question fields"""
        try:
            result = await self.questions.update_one(
                {"id": question_id},
                {"$set": kwargs}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error updating question: {e}")
            return False
    
    async def delete_question(self, question_id: int) -> bool:
        """Delete a question"""
        try:
            result = await self.questions.delete_one({"id": question_id})
            return result.deleted_count > 0
        except Exception as e:
            logger.error(f"❌ Error deleting question: {e}")
            return False
    
    async def count_quiz_questions(self, quiz_id: int) -> int:
        """Count questions in a quiz"""
        try:
            return await self.questions.count_documents({"quiz_id": quiz_id})
        except Exception as e:
            logger.error(f"❌ Error counting questions: {e}")
            return 0
    
    # ========== BROADCAST OPERATIONS ==========
    
    async def add_broadcast_user(self, chat_id: int) -> bool:
        """Add user to broadcast list"""
        try:
            await self.broadcast_users.update_one(
                {"chat_id": chat_id},
                {"$set": {"chat_id": chat_id, "added_at": datetime.utcnow()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"❌ Error adding broadcast user: {e}")
            return False
    
    async def add_broadcast_group(self, chat_id: int) -> bool:
        """Add group to broadcast list"""
        try:
            await self.broadcast_groups.update_one(
                {"chat_id": chat_id},
                {"$set": {"chat_id": chat_id, "added_at": datetime.utcnow()}},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"❌ Error adding broadcast group: {e}")
            return False
    
    async def get_broadcast_users(self) -> List[int]:
        """Get all broadcast user IDs"""
        try:
            cursor = self.broadcast_users.find({})
            docs = await cursor.to_list(length=None)
            return [doc["chat_id"] for doc in docs]
        except Exception as e:
            logger.error(f"❌ Error getting broadcast users: {e}")
            return []
    
    async def get_broadcast_groups(self) -> List[int]:
        """Get all broadcast group IDs"""
        try:
            cursor = self.broadcast_groups.find({})
            docs = await cursor.to_list(length=None)
            return [doc["chat_id"] for doc in docs]
        except Exception as e:
            logger.error(f"❌ Error getting broadcast groups: {e}")
            return []
    
    # ========== AUTORUN OPERATIONS ==========
    
    async def create_autorun(self, quiz_id: int, interval_minutes: int,
                           schedule_time: Optional[str] = None) -> Optional[int]:
        """Create an autorun task"""
        try:
            # Get next autorun id
            last_ar = await self.autoruns.find_one(sort=[("id", DESCENDING)])
            ar_id = (last_ar["id"] + 1) if last_ar else 1
            
            autorun_doc = {
                "id": ar_id,
                "quiz_id": quiz_id,
                "interval_minutes": interval_minutes,
                "schedule_time": schedule_time,
                "next_run": None,
                "active": 1,
                "created_at": datetime.utcnow()
            }
            
            await self.autoruns.insert_one(autorun_doc)
            logger.info(f"✅ Autorun created: ID={ar_id}, Quiz={quiz_id}")
            return ar_id
        except Exception as e:
            logger.error(f"❌ Error creating autorun: {e}")
            return None
    
    async def get_autorun(self, autorun_id: int) -> Optional[Dict]:
        """Get autorun by ID"""
        try:
            return await self.autoruns.find_one({"id": autorun_id})
        except Exception as e:
            logger.error(f"❌ Error getting autorun: {e}")
            return None
    
    async def get_active_autoruns(self) -> List[Dict]:
        """Get all active autoruns"""
        try:
            cursor = self.autoruns.find({"active": 1})
            return await cursor.to_list(length=None)
        except Exception as e:
            logger.error(f"❌ Error getting active autoruns: {e}")
            return []
    
    async def update_autorun(self, autorun_id: int, **kwargs) -> bool:
        """Update autorun fields"""
        try:
            result = await self.autoruns.update_one(
                {"id": autorun_id},
                {"$set": kwargs}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error updating autorun: {e}")
            return False
    
    async def deactivate_autorun(self, autorun_id: int) -> bool:
        """Deactivate an autorun"""
        try:
            return await self.update_autorun(autorun_id, active=0)
        except Exception as e:
            logger.error(f"❌ Error deactivating autorun: {e}")
            return False
    
    async def deactivate_quiz_autoruns(self, quiz_id: int) -> bool:
        """Deactivate all autoruns for a quiz"""
        try:
            result = await self.autoruns.update_many(
                {"quiz_id": quiz_id, "active": 1},
                {"$set": {"active": 0}}
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"❌ Error deactivating quiz autoruns: {e}")
            return False


# Global instance
db_manager = MongoDBManager()


async def init_mongodb():
    """Initialize MongoDB connection"""
    await db_manager.connect()


async def close_mongodb():
    """Close MongoDB connection"""
    await db_manager.close()
