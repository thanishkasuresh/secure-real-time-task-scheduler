import sqlite3
import hashlib
import getpass
from dataclasses import dataclass
from datetime import datetime

DB_FILE = "task_scheduler.db"


class DatabaseManager:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE)
        self.conn.row_factory = sqlite3.Row
        self.create_tables()

    def create_tables(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tasks(
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            priority INTEGER NOT NULL,
            execution_time INTEGER NOT NULL,
            deadline INTEGER NOT NULL,
            arrival_time INTEGER NOT NULL,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS execution_logs(
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            algorithm TEXT NOT NULL,
            waiting_time REAL NOT NULL,
            turnaround_time REAL NOT NULL,
            response_time REAL NOT NULL,
            deadline_status TEXT NOT NULL,
            executed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS security_logs(
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            event TEXT NOT NULL,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS performance_metrics(
            metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
            algorithm TEXT NOT NULL,
            average_waiting REAL NOT NULL,
            average_turnaround REAL NOT NULL,
            average_response REAL NOT NULL,
            deadline_misses INTEGER NOT NULL,
            completed_tasks INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """)
        self.conn.commit()

    def close(self):
        self.conn.close()


class SecurityManager:
    def __init__(self, db):
        self.db = db

    @staticmethod
    def hash_password(password):
        return hashlib.sha256(password.encode()).hexdigest()

    def create_default_users(self):
        users = [
            ("admin", "admin123", "Administrator"),
            ("user", "user123", "Normal User")
        ]
        for username, password, role in users:
            row = self.db.conn.execute(
                "SELECT user_id FROM users WHERE username=?",
                (username,)
            ).fetchone()
            if row is None:
                self.db.conn.execute(
                    "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                    (username, self.hash_password(password), role)
                )
        self.db.conn.commit()

    def authenticate(self, username, password):
        row = self.db.conn.execute(
            "SELECT username,password_hash,role FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if row and row["password_hash"] == self.hash_password(password):
            self.log(username, "Successful login", "INFO")
            return {"username": row["username"], "role": row["role"]}

        self.log(username, "Failed login attempt", "HIGH")
        return None

    def log(self, username, event, severity):
        self.db.conn.execute(
            "INSERT INTO security_logs(username,event,severity,created_at) VALUES(?,?,?,?)",
            (username, event, severity, datetime.now().isoformat(timespec="seconds"))
        )
        self.db.conn.commit()


@dataclass
class Task:
    task_id: int
    name: str
    priority: int
    execution_time: int
    deadline: int
    arrival_time: int
    status: str


class TaskManager:
    def __init__(self, db, security):
        self.db = db
        self.security = security

    def validate(self, name, priority, execution, deadline, arrival):
        if not name:
            return False, "Task name cannot be empty."
        if not 1 <= priority <= 5:
            return False, "Priority must be between 1 and 5."
        if execution <= 0:
            return False, "Execution time must be greater than 0."
        if deadline <= 0:
            return False, "Deadline must be greater than 0."
        if arrival < 0:
            return False, "Arrival time cannot be negative."
        return True, "Valid"

    def add_task(self, user, name, priority, execution, deadline, arrival):
        valid, message = self.validate(
            name, priority, execution, deadline, arrival
        )
        if not valid:
            self.security.log(
                user["username"], "Rejected task: " + message, "HIGH"
            )
            print("\n[SECURITY] Task rejected:", message)
            return

        self.db.conn.execute(
            """INSERT INTO tasks
            (name,priority,execution_time,deadline,arrival_time,status)
            VALUES(?,?,?,?,?,?)""",
            (name, priority, execution, deadline, arrival, "READY")
        )
        self.db.conn.commit()
        print("\n[OK] Task created successfully.")

    def seed_demo_tasks(self):
        count = self.db.conn.execute(
            "SELECT COUNT(*) AS n FROM tasks"
        ).fetchone()["n"]

        if count:
            return

        demo = [
            ("Task 1", 3, 3, 8, 0),
            ("Task 2", 1, 2, 5, 0),
            ("Task 3", 5, 4, 12, 0),
            ("Task 4", 2, 1, 6, 0)
        ]

        self.db.conn.executemany(
            """INSERT INTO tasks
            (name,priority,execution_time,deadline,arrival_time,status)
            VALUES(?,?,?,?,?,?)""",
            [(n,p,e,d,a,"READY") for n,p,e,d,a in demo]
        )
        self.db.conn.commit()

    def get_tasks(self):
        rows = self.db.conn.execute(
            """SELECT task_id,name,priority,execution_time,
                      deadline,arrival_time,status
               FROM tasks ORDER BY task_id"""
        ).fetchall()

        return [Task(
            r["task_id"], r["name"], r["priority"],
            r["execution_time"], r["deadline"],
            r["arrival_time"], r["status"]
        ) for r in rows]


class Scheduler:
    def schedule(self, tasks):
        raise NotImplementedError


class FCFSScheduler(Scheduler):
    def schedule(self, tasks):
        return sorted(tasks, key=lambda t: (t.arrival_time, t.task_id))


class PriorityScheduler(Scheduler):
    def schedule(self, tasks):
        return sorted(
            tasks,
            key=lambda t: (-t.priority, t.arrival_time, t.task_id)
        )


class EDFScheduler(Scheduler):
    def schedule(self, tasks):
        return sorted(
            tasks,
            key=lambda t: (t.deadline, t.arrival_time, t.task_id)
        )


class PerformanceManager:
    def __init__(self, db):
        self.db = db

    def save(self, algorithm, results):
        if not results:
            return

        avg_wait = sum(r["waiting"] for r in results) / len(results)
        avg_turn = sum(r["turnaround"] for r in results) / len(results)
        avg_response = sum(r["response"] for r in results) / len(results)
        misses = sum(
            r["deadline_status"] == "Deadline Missed" for r in results
        )
        completed = len(results) - misses

        self.db.conn.execute(
            """INSERT INTO performance_metrics
            (algorithm,average_waiting,average_turnaround,
             average_response,deadline_misses,completed_tasks,created_at)
            VALUES(?,?,?,?,?,?,?)""",
            (
                algorithm, avg_wait, avg_turn, avg_response,
                misses, completed,
                datetime.now().isoformat(timespec="seconds")
            )
        )
        self.db.conn.commit()

    def show(self, results):
        if not results:
            return

        avg_wait = sum(r["waiting"] for r in results) / len(results)
        avg_turn = sum(r["turnaround"] for r in results) / len(results)
        avg_response = sum(r["response"] for r in results) / len(results)
        misses = sum(
            r["deadline_status"] == "Deadline Missed" for r in results
        )

        print("\n========== PERFORMANCE ==========")
        print(f"Average Waiting Time  : {avg_wait:.2f}")
        print(f"Average Turnaround    : {avg_turn:.2f}")
        print(f"Average Response Time : {avg_response:.2f}")
        print(f"Deadline Misses       : {misses}")
        print(f"Completed Tasks       : {len(results)-misses}")
        print("==================================")


class ExecutionEngine:
    def __init__(self, db):
        self.db = db

    def run(self, tasks, algorithm):
        current = 0
        results = []

        print("\n========== EXECUTION ==========")
        print("Algorithm:", algorithm)

        for task in tasks:
            start = max(current, task.arrival_time)
            waiting = start - task.arrival_time
            response = waiting
            finish = start + task.execution_time
            turnaround = finish - task.arrival_time

            status = (
                "Completed"
                if finish <= task.deadline
                else "Deadline Missed"
            )

            current = finish

            result = {
                "task_id": task.task_id,
                "name": task.name,
                "waiting": waiting,
                "turnaround": turnaround,
                "response": response,
                "deadline_status": status
            }
            results.append(result)

            print("\nTask:", task.name)
            print("Priority:", task.priority)
            print("Execution time:", task.execution_time)
            print("Deadline:", task.deadline)
            print("Waiting time:", waiting)
            print("Turnaround time:", turnaround)
            print("Response time:", response)
            print("Status:", status)

            self.db.conn.execute(
                """INSERT INTO execution_logs
                (task_id,algorithm,waiting_time,turnaround_time,
                 response_time,deadline_status,executed_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    task.task_id, algorithm, waiting, turnaround,
                    response, status,
                    datetime.now().isoformat(timespec="seconds")
                )
            )

        self.db.conn.commit()
        return results


class Application:
    def __init__(self):
        self.db = DatabaseManager()
        self.security = SecurityManager(self.db)
        self.tasks = TaskManager(self.db, self.security)
        self.performance = PerformanceManager(self.db)
        self.engine = ExecutionEngine(self.db)

        self.security.create_default_users()
        self.tasks.seed_demo_tasks()

    def login(self):
        print("\n========== LOGIN ==========")
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")

        user = self.security.authenticate(username, password)

        if user:
            print("Login successful!")
            print("Role:", user["role"])
            return user

        print("Invalid username or password.")
        return None

    def view_tasks(self):
        tasks = self.tasks.get_tasks()

        print("\n============== TASKS ==============")
        print(
            f"{'ID':<4}{'Name':<12}{'Priority':<10}"
            f"{'Exec':<8}{'Deadline':<10}{'Status'}"
        )
        print("-" * 58)

        for t in tasks:
            print(
                f"{t.task_id:<4}{t.name:<12}{t.priority:<10}"
                f"{t.execution_time:<8}{t.deadline:<10}{t.status}"
            )

    def create_task(self, user):
        print("\n========== CREATE TASK ==========")

        try:
            name = input("Task name: ").strip()
            priority = int(input("Priority (1-5): "))
            execution = int(input("Execution time: "))
            deadline = int(input("Deadline: "))
            arrival = int(input("Arrival time: "))
        except ValueError:
            self.security.log(
                user["username"], "Invalid numeric task input", "HIGH"
            )
            print("[SECURITY] Invalid input.")
            return

        self.tasks.add_task(
            user, name, priority, execution, deadline, arrival
        )

    def choose_scheduler(self):
        print("\n========== ALGORITHM ==========")
        print("1. FCFS")
        print("2. Priority Scheduling")
        print("3. Earliest Deadline First (EDF)")

        choice = input("Choose: ").strip()

        if choice == "1":
            return FCFSScheduler(), "FCFS"
        if choice == "2":
            return PriorityScheduler(), "Priority"
        if choice == "3":
            return EDFScheduler(), "EDF"

        print("Invalid choice.")
        return None, None

    def run_scheduler(self):
        tasks = self.tasks.get_tasks()
        scheduler, algorithm = self.choose_scheduler()

        if scheduler is None:
            return

        ordered = scheduler.schedule(tasks)

        print("\nExecution order:")
        print(" -> ".join(t.name for t in ordered))

        results = self.engine.run(ordered, algorithm)
        self.performance.save(algorithm, results)
        self.performance.show(results)

    def security_logs(self):
        rows = self.db.conn.execute(
            """SELECT username,event,severity,created_at
               FROM security_logs ORDER BY log_id DESC LIMIT 20"""
        ).fetchall()

        print("\n========== SECURITY LOGS ==========")

        for r in rows:
            print(
                f"[{r['severity']}] {r['created_at']} | "
                f"{r['username']} | {r['event']}"
            )

    def menu(self, user):
        while True:
            print("\n====================================")
            print(" SECURE REAL-TIME TASK SCHEDULER")
            print("====================================")
            print(f"User: {user['username']} | Role: {user['role']}")
            print("1. View tasks")
            print("2. Create task")
            print("3. Run scheduler")
            print("4. Security logs")
            print("5. Logout")

            choice = input("Enter choice: ").strip()

            if choice == "1":
                self.view_tasks()

            elif choice == "2":
                if user["role"] != "Administrator":
                    print("[ACCESS DENIED]")
                    self.security.log(
                        user["username"],
                        "Unauthorized task creation attempt",
                        "HIGH"
                    )
                else:
                    self.create_task(user)

            elif choice == "3":
                self.run_scheduler()

            elif choice == "4":
                self.security_logs()

            elif choice == "5":
                self.security.log(
                    user["username"], "User logged out", "INFO"
                )
                break

            else:
                print("Invalid choice.")

    def run(self):
        print("\n==========================================")
        print(" SECURE DATABASE-INTEGRATED")
        print(" REAL-TIME TASK SCHEDULER")
        print("==========================================")
        print("\nDemo accounts:")
        print("Administrator: admin / admin123")
        print("Normal User:   user / user123")

        while True:
            user = self.login()

            if user:
                self.menu(user)

            again = input("\nLogin again? (y/n): ").strip().lower()
            if again != "y":
                break

        self.db.close()
        print("Application closed.")


if __name__ == "__main__":
    Application().run()
