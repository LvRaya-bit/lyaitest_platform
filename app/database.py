import os
import sqlite3
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "lyaitest.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            session_type TEXT DEFAULT 'normal',
            knowledge_base_id TEXT,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            metadata TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            knowledge_base_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            content TEXT,
            vector_indexed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_bases(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_interfaces (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            method TEXT NOT NULL,
            headers TEXT DEFAULT '{}',
            params TEXT DEFAULT '{}',
            body TEXT DEFAULT '{}',
            project_id TEXT,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_scenarios (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_scene_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            scenario_id TEXT NOT NULL,
            interface_id TEXT NOT NULL,
            step_order INTEGER NOT NULL,
            request_data TEXT DEFAULT '{}',
            extract_vars TEXT DEFAULT '{}',
            assertions TEXT DEFAULT '{}',
            FOREIGN KEY (scenario_id) REFERENCES api_scenarios(id),
            FOREIGN KEY (interface_id) REFERENCES api_interfaces(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_envs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            base_url TEXT NOT NULL,
            variables TEXT DEFAULT '{}',
            project_id TEXT,
            is_default INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_cases (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            steps TEXT DEFAULT '[]',
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_suites (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            case_ids TEXT DEFAULT '[]',
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            suite_id TEXT NOT NULL,
            task_type TEXT DEFAULT 'manual',
            cron_expression TEXT,
            status TEXT DEFAULT 'pending',
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (suite_id) REFERENCES web_suites(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS web_exploration_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            start_url TEXT NOT NULL,
            runner_task_id TEXT,
            status TEXT DEFAULT 'pending',
            max_depth INTEGER DEFAULT 3,
            max_actions INTEGER DEFAULT 100,
            result_path TEXT,
            summary TEXT DEFAULT '{}',
            error TEXT,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            task_id TEXT,
            test_type TEXT NOT NULL,
            test_name TEXT,
            url TEXT,
            status_code INTEGER,
            response_time REAL,
            title TEXT,
            screenshot TEXT,
            error TEXT,
            status TEXT DEFAULT 'running',
            total_cases INTEGER DEFAULT 0,
            passed_cases INTEGER DEFAULT 0,
            failed_cases INTEGER DEFAULT 0,
            duration REAL,
            logs TEXT DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_test_results (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            scenario_id TEXT,
            step_index INTEGER,
            interface_name TEXT,
            request_url TEXT,
            request_method TEXT,
            request_body TEXT,
            response_status INTEGER,
            response_body TEXT,
            response_time REAL,
            assertions TEXT,
            passed INTEGER DEFAULT 1,
            error_message TEXT,
            created_at TEXT NOT NULL,
        FOREIGN KEY (report_id) REFERENCES reports(id)
        )
    """)

    # APP 自动化与测试用例生成扩展表。保留原有表结构，所有新能力独立存储。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_devices (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            server_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:4723',
            device_udid TEXT NOT NULL,
            app_package TEXT NOT NULL,
            app_activity TEXT NOT NULL,
            test_phone TEXT,
            test_password_env TEXT DEFAULT 'TEST_PASSWORD',
            capabilities TEXT DEFAULT '{}',
            status TEXT DEFAULT 'unknown',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_exploration_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            runner_task_id TEXT,
            status TEXT DEFAULT 'pending',
            max_depth INTEGER DEFAULT 3,
            max_actions INTEGER DEFAULT 100,
            result_path TEXT,
            summary TEXT DEFAULT '{}',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (device_id) REFERENCES app_devices(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generated_case_sets (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_path TEXT,
            source_ref TEXT,
            status TEXT DEFAULT 'draft',
            case_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS generated_test_cases (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            set_id TEXT NOT NULL,
            title TEXT NOT NULL,
            module TEXT,
            priority TEXT DEFAULT 'P1',
            test_type TEXT DEFAULT '功能测试',
            preconditions TEXT,
            steps TEXT DEFAULT '[]',
            expected TEXT,
            source_ref TEXT,
            automation_status TEXT DEFAULT 'draft',
            metadata TEXT DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (set_id) REFERENCES generated_case_sets(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_test_runs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            device_id TEXT NOT NULL,
            case_set_id TEXT,
            runner_task_id TEXT,
            status TEXT DEFAULT 'pending',
            report_path TEXT,
            summary TEXT DEFAULT '{}',
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (device_id) REFERENCES app_devices(id),
            FOREIGN KEY (case_set_id) REFERENCES generated_case_sets(id)
        )
    """)

    # pi-agent 自愈任务：Runner 的内存 JOBS 重启即丢，历史必须落在平台侧。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pi_agent_tasks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            case_id TEXT,
            case_name TEXT,
            start_url TEXT NOT NULL,
            storage_state TEXT,
            runner_task_id TEXT,
            status TEXT DEFAULT 'pending',
            exit_code INTEGER,
            final_url TEXT,
            page_title TEXT,
            fixed_test TEXT,
            memory TEXT,
            log TEXT,
            error TEXT,
            project_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ 数据库初始化完成")
