import unittest
import os
import uuid
import json
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, ExecutionTrace, HumanCorrection

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class TestAnalyticsMetrics(unittest.TestCase):
    def setUp(self):
        Base.metadata.create_all(bind=engine)
        self.db = TestingSessionLocal()
        
        # Insert a scenario: general task, tools available but not called
        trace1 = ExecutionTrace(
            id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            task_type_classified="general",
            task_type_final="general",
            model_used="gemma4-e4b",
            model_selected="gemma4-e4b",
            planner_enabled=False,
            tools_available=5,
            tools_called=0,  # SILENT MISS!
            tools_called_names="[]",
            duration_ms=1000,
            actions_log="[]",
            final_status="stub_response",
            created_at=datetime.utcnow()
        )
        
        # Insert a normal scenario: coding task, tools used
        trace2 = ExecutionTrace(
            id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            task_type_classified="coding",
            task_type_final="coding",
            model_used="qwen2.5-coder:32b",
            model_selected="qwen2.5-coder:32b",
            planner_enabled=True,
            tools_available=23,
            tools_called=2,
            tools_called_names='["list_tasks", "add_cron_task"]',
            duration_ms=5000,
            actions_log='["list_tasks", "add_cron_task"]',
            final_status="success",
            created_at=datetime.utcnow()
        )
        # Insert a scenario: fake success (tool verified = false)
        trace3 = ExecutionTrace(
            id=str(uuid.uuid4()),
            session_id=str(uuid.uuid4()),
            task_type_classified="coding",
            task_type_final="coding",
            model_used="qwen2.5-coder:32b",
            model_selected="qwen2.5-coder:32b",
            planner_enabled=True,
            tools_available=23,
            tools_called=1,
            tools_called_names='["add_cron_task"]',
            duration_ms=2000,
            actions_log='["add_cron_task"]',
            final_status="success",
            tool_verified=False,
            tool_verification_details="Cron task not found in scheduler despite success string.",
            created_at=datetime.utcnow()
        )
        
        self.db.add(trace1)
        self.db.add(trace2)
        self.db.add(trace3)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)  # not needed after last test, but harmless

    def test_silent_miss_routing_accuracy(self):
        # We simulate the API query behavior
        # The query should group by task_type_classified
        from sqlalchemy import text
        silent_miss_sql = """
            SELECT task_type_classified, COUNT(*) as total,
                   SUM(CASE WHEN tools_available > 0 AND tools_called = 0 THEN 1 ELSE 0 END) as silent_miss
            FROM execution_traces
            WHERE task_type_classified IS NOT NULL
            GROUP BY task_type_classified
        """
        res = self.db.execute(text(silent_miss_sql)).mappings().all()
        
        # Verify it grouped by task_type_classified
        self.assertEqual(len(res), 2)
        
        # Verify the "general" type has 1 silent miss
        general_row = next(r for r in res if r['task_type_classified'] == 'general')
        self.assertEqual(general_row['total'], 1)
        self.assertEqual(general_row['silent_miss'], 1)
        
        # Verify the "coding" type has 0 silent misses
        coding_row = next(r for r in res if r['task_type_classified'] == 'coding')
        self.assertEqual(coding_row['total'], 2)
        self.assertEqual(coding_row['silent_miss'], 0)

    def test_unverified_success(self):
        from sqlalchemy import text
        unverified_sql = """
            SELECT task_type_final, COUNT(*) as total,
                   SUM(CASE WHEN tool_verified = 0 THEN 1 ELSE 0 END) as unverified_success,
                   GROUP_CONCAT(tool_verification_details, '; ') as details
            FROM execution_traces
            WHERE final_status = 'success' AND tool_verified IS NOT NULL
            GROUP BY task_type_final
        """
        res = self.db.execute(text(unverified_sql)).mappings().all()
        
        self.assertEqual(len(res), 1)
        coding_row = res[0]
        self.assertEqual(coding_row['task_type_final'], 'coding')
        # We inserted 1 success with tool_verified=False. (trace2 doesn't have tool_verified set, so it's NULL, won't be counted in this query based on WHERE tool_verified IS NOT NULL)
        # Wait, if trace2 doesn't have tool_verified set, it won't be in the result. So total is 1.
        self.assertEqual(coding_row['total'], 1)
        self.assertEqual(coding_row['unverified_success'], 1)
        self.assertIn("Cron task not found", coding_row['details'])

if __name__ == "__main__":
    unittest.main()
