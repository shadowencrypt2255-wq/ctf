import http.server
import socketserver
import json
import sqlite3
import os
import mimetypes
from urllib.parse import urlparse, parse_qs
import base64
import uuid

# Force correct mimetypes for Windows registry bugs
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('application/javascript', '.js')

PORT = int(os.environ.get('PORT', 8080))
DIRECTORY = "public"

def init_db():
    conn = sqlite3.connect('ctf.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            registration_type TEXT NOT NULL,
            team_name TEXT,
            name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            whatsapp TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            points INTEGER NOT NULL,
            flag TEXT NOT NULL,
            file_url TEXT,
            is_active INTEGER DEFAULT 1
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            challenge_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, challenge_id)
        )
    ''')
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('login_enabled', '0')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ctf_running', '1')")
    # Default admin user (for demo purposes)
    c.execute("INSERT OR IGNORE INTO users (registration_type, name, username, whatsapp, email, password) VALUES ('admin', 'Admin', 'admin', '0000', 'admin@ctf.com', 'admin123')")
    conn.commit()
    conn.close()

class MyRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def do_GET(self):
        if self.path == '/api/status':
            conn = sqlite3.connect('ctf.db')
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key='login_enabled'")
            login_row = c.fetchone()
            c.execute("SELECT value FROM settings WHERE key='ctf_running'")
            ctf_row = c.fetchone()
            ctf_running = ctf_row[0] == '1' if ctf_row else True
            conn.close()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'login_enabled': login_row[0] == '1', 'ctf_running': ctf_running}).encode())
        elif self.path.startswith('/api/admin/'):
            parsed_path = urlparse(self.path)
            query = parse_qs(parsed_path.query)
            admin_user = query.get('admin_username', [''])[0]
            admin_pass = query.get('admin_password', [''])[0]
            
            conn = sqlite3.connect('ctf.db')
            c = conn.cursor()
            c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (admin_user, admin_pass))
            admin = c.fetchone()
            
            if not admin:
                conn.close()
                return self.send_error_json(403, "Unauthorized")
                
            if parsed_path.path == '/api/admin/users':
                c.execute("SELECT id, registration_type, team_name, name, username, whatsapp, email FROM users WHERE registration_type != 'admin'")
                users = [{"id": row[0], "type": row[1], "team_name": row[2], "name": row[3], "username": row[4], "whatsapp": row[5], "email": row[6]} for row in c.fetchall()]
                conn.close()
                self.send_success_json({"users": users})
            elif parsed_path.path == '/api/admin/challenges':
                c.execute("SELECT id, title, category, description, points, flag, file_url, is_active, link_url FROM challenges")
                challenges = [{"id": row[0], "title": row[1], "category": row[2], "description": row[3], "points": row[4], "flag": row[5], "file_url": row[6], "is_active": row[7], "link_url": row[8]} for row in c.fetchall()]
                conn.close()
                self.send_success_json({"challenges": challenges})
            else:
                conn.close()
                self.send_error_json(404, "Not Found")
        elif self.path == '/api/challenges':
            conn = sqlite3.connect('ctf.db')
            c = conn.cursor()
            c.execute("SELECT id, title, category, description, points, file_url, link_url FROM challenges WHERE is_active=1 OR is_active IS NULL")
            challenges = [{"id": row[0], "title": row[1], "category": row[2], "description": row[3], "points": row[4], "file_url": row[5], "link_url": row[6]} for row in c.fetchall()]
            conn.close()
            self.send_success_json({"challenges": challenges})
        elif self.path == '/api/scoreboard':
            conn = sqlite3.connect('ctf.db')
            c = conn.cursor()
            query = '''
                SELECT u.id, u.username, u.team_name, u.registration_type, IFNULL(SUM(c.points), 0) as score
                FROM users u
                LEFT JOIN submissions s ON u.id = s.user_id
                LEFT JOIN challenges c ON s.challenge_id = c.id
                WHERE u.registration_type != 'admin'
                GROUP BY u.id
                ORDER BY score DESC, u.id ASC
            '''
            c.execute(query)
            scoreboard = [{"id": row[0], "username": row[1], "team_name": row[2], "type": row[3], "score": row[4]} for row in c.fetchall()]
            conn.close()
            self.send_success_json({"scoreboard": scoreboard})
        else:
            super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = {}
        try:
            if body:
                data = json.loads(body)
        except:
            pass

        if self.path == '/api/register':
            self.handle_register(data)
        elif self.path == '/api/login':
            self.handle_login(data)
        elif self.path == '/api/admin/toggle':
            self.handle_admin_toggle(data)
        elif self.path == '/api/admin/challenges/add':
            self.handle_admin_add_challenge(data)
        elif self.path == '/api/admin/users/delete':
            self.handle_admin_delete_user(data)
        elif self.path == '/api/admin/users/delete_all':
            self.handle_admin_delete_all_users(data)
        elif self.path == '/api/admin/challenges/toggle_visibility':
            self.handle_admin_toggle_challenge_visibility(data)
        elif self.path == '/api/admin/challenges/delete':
            self.handle_admin_delete_challenge(data)
        elif self.path == '/api/admin/challenges/edit':
            self.handle_admin_edit_challenge(data)
        elif self.path == '/api/admin/ctf_toggle':
            self.handle_admin_ctf_toggle(data)
        elif self.path == '/api/admin/change_password':
            self.handle_admin_change_password(data)
        elif self.path == '/api/submit':
            self.handle_submit_flag(data)
        else:
            self.send_response(404)
            self.end_headers()

    def handle_register(self, data):
        reg_type = data.get('type')
        if reg_type not in ['individual', 'team']:
            return self.send_error_json(400, "Invalid registration type")

        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        
        try:
            if reg_type == 'individual':
                u = data.get('user', {})
                c.execute("INSERT INTO users (registration_type, name, username, whatsapp, email, password) VALUES (?, ?, ?, ?, ?, ?)",
                          ('individual', u.get('name'), u.get('username'), u.get('whatsapp'), u.get('email'), u.get('password')))
            elif reg_type == 'team':
                team_name = data.get('team_name')
                u1 = data.get('member1', {})
                u2 = data.get('member2', {})
                if not team_name:
                    raise Exception("Team name is required for team registration.")
                
                c.execute("INSERT INTO users (registration_type, team_name, name, username, whatsapp, email, password) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          ('team', team_name, u1.get('name'), u1.get('username'), u1.get('whatsapp'), u1.get('email'), u1.get('password')))
                c.execute("INSERT INTO users (registration_type, team_name, name, username, whatsapp, email, password) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          ('team', team_name, u2.get('name'), u2.get('username'), u2.get('whatsapp'), u2.get('email'), u2.get('password')))
            
            conn.commit()
            self.send_success_json({"message": "Successfully registered!"})
        except sqlite3.IntegrityError as e:
            self.send_error_json(400, "Username or Email already exists. You cannot register again.")
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def handle_login(self, data):
        username = data.get('username')
        password = data.get('password')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        
        # Check if login is enabled (unless it's admin)
        c.execute("SELECT value FROM settings WHERE key='login_enabled'")
        login_enabled_val = c.fetchone()[0]
        
        c.execute("SELECT id, registration_type, username, team_name FROM users WHERE username=? AND password=?", (username, password))
        user = c.fetchone()
        conn.close()

        if not user:
            return self.send_error_json(401, "Invalid credentials.")

        if user[1] != 'admin' and login_enabled_val != '1':
            return self.send_error_json(403, "Login is currently disabled by administrators. Registrations are still open!")

        self.send_success_json({
            "message": "Login successful!",
            "user": {
                "id": user[0],
                "type": user[1],
                "username": user[2],
                "team_name": user[3]
            }
        })

    def handle_admin_toggle(self, data):
        # Very simple admin auth checking logic
        username = data.get('admin_username')
        password = data.get('admin_password')
        enable_login = data.get('enable_login') # Boolean
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
        
        c.execute("UPDATE settings SET value=? WHERE key='login_enabled'", ('1' if enable_login else '0',))
        conn.commit()
        conn.close()
        
        status = "enabled" if enable_login else "disabled"
        self.send_success_json({"message": f"Login has been {status} successfully!"})

    def handle_admin_ctf_toggle(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        ctf_running = data.get('ctf_running') # Boolean
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
        
        c.execute("UPDATE settings SET value=? WHERE key='ctf_running'", ('1' if ctf_running else '0',))
        conn.commit()
        conn.close()
        
        status = "started" if ctf_running else "stopped"
        self.send_success_json({"message": f"CTF has been {status} successfully!"})

    def handle_admin_change_password(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        new_password = data.get('new_password')
        
        if not new_password or len(new_password) < 4:
            return self.send_error_json(400, "Password must be at least 4 characters")

        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized/Wrong current password")
        
        c.execute("UPDATE users SET password=? WHERE id=?", (new_password, admin[0]))
        conn.commit()
        conn.close()
        
        self.send_success_json({"message": "Admin password updated successfully!"})

    def handle_admin_add_challenge(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
            
        file_url = None
        file_data = data.get('file_data')
        file_name = data.get('file_name')
        
        if file_data and file_name:
            try:
                header, encoded = file_data.split(",", 1) if "," in file_data else ("", file_data)
                ext = file_name.split('.')[-1] if '.' in file_name else 'bin'
                filename = f"{uuid.uuid4().hex}.{ext}"
                os.makedirs(os.path.join(DIRECTORY, "uploads"), exist_ok=True)
                filepath = os.path.join(DIRECTORY, "uploads", filename)
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(encoded))
                file_url = f"uploads/{filename}"
            except Exception as e:
                return self.send_error_json(400, f"Error uploading file: {e}")

        try:
            c.execute("INSERT INTO challenges (title, category, description, points, flag, file_url, is_active, link_url) VALUES (?, ?, ?, ?, ?, ?, 1, ?)",
                      (data.get('title'), data.get('category'), data.get('description'), data.get('points'), data.get('flag'), file_url, data.get('link_url')))
            conn.commit()
            self.send_success_json({"message": "Challenge added successfully!"})
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def handle_admin_edit_challenge(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        challenge_id = data.get('challenge_id')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")

        # Basic fields
        title = data.get('title')
        category = data.get('category')
        description = data.get('description')
        points = data.get('points')
        flag = data.get('flag')
        link_url = data.get('link_url')
        
        # File handling
        file_data = data.get('file_data')
        file_name = data.get('file_name')
        
        try:
            if file_data and file_name:
                header, encoded = file_data.split(",", 1) if "," in file_data else ("", file_data)
                ext = file_name.split('.')[-1] if '.' in file_name else 'bin'
                filename = f"{uuid.uuid4().hex}.{ext}"
                os.makedirs(os.path.join(DIRECTORY, "uploads"), exist_ok=True)
                filepath = os.path.join(DIRECTORY, "uploads", filename)
                with open(filepath, "wb") as f:
                    f.write(base64.b64decode(encoded))
                file_url = f"uploads/{filename}"
                
                c.execute("UPDATE challenges SET title=?, category=?, description=?, points=?, flag=?, link_url=?, file_url=? WHERE id=?",
                          (title, category, description, points, flag, link_url, file_url, challenge_id))
            else:
                # Update without changing file
                c.execute("UPDATE challenges SET title=?, category=?, description=?, points=?, flag=?, link_url=? WHERE id=?",
                          (title, category, description, points, flag, link_url, challenge_id))
            
            conn.commit()
            self.send_success_json({"message": "Challenge updated successfully!"})
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def handle_submit_flag(self, data):
        username = data.get('username')
        challenge_id = data.get('challenge_id')
        flag_submitted = data.get('flag')

        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()

        c.execute("SELECT value FROM settings WHERE key='ctf_running'")
        ctf_row = c.fetchone()
        if ctf_row and ctf_row[0] == '0':
            conn.close()
            return self.send_error_json(403, "The CTF has stopped. Submissions are no longer accepted.")
        
        # Verify user
        c.execute("SELECT id FROM users WHERE username=?", (username,))
        user = c.fetchone()
        if not user:
            conn.close()
            return self.send_error_json(401, "Unauthorized or user not found")
        user_id = user[0]

        # Check challenge flag
        c.execute("SELECT flag FROM challenges WHERE id=?", (challenge_id,))
        challenge = c.fetchone()
        if not challenge:
            conn.close()
            return self.send_error_json(404, "Challenge not found")

        actual_flag = challenge[0]
        
        if flag_submitted.strip() == actual_flag.strip():
            # record submission
            try:
                c.execute("INSERT INTO submissions (user_id, challenge_id) VALUES (?, ?)", (user_id, challenge_id))
                conn.commit()
                self.send_success_json({"message": "Correct flag! Points awarded.", "correct": True})
            except sqlite3.IntegrityError:
                self.send_error_json(400, "Flag already submitted for this challenge")
        else:
            self.send_success_json({"message": "Incorrect flag. Try again!", "correct": False})
        
        conn.close()

    def handle_admin_delete_user(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        user_id = data.get('user_id')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
            
        try:
            c.execute("DELETE FROM users WHERE id=? AND registration_type!='admin'", (user_id,))
            c.execute("DELETE FROM submissions WHERE user_id=?", (user_id,))
            conn.commit()
            self.send_success_json({"message": "User deleted successfully!"})
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def handle_admin_delete_all_users(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
            
        try:
            c.execute("DELETE FROM users WHERE registration_type!='admin'")
            c.execute("DELETE FROM submissions")
            conn.commit()
            self.send_success_json({"message": "All users deleted successfully!"})
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def handle_admin_toggle_challenge_visibility(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        challenge_id = data.get('challenge_id')
        is_active = data.get('is_active')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
            
        try:
            c.execute("UPDATE challenges SET is_active=? WHERE id=?", (1 if is_active else 0, challenge_id))
            conn.commit()
            self.send_success_json({"message": "Challenge updated successfully!"})
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def handle_admin_delete_challenge(self, data):
        username = data.get('admin_username')
        password = data.get('admin_password')
        challenge_id = data.get('challenge_id')
        
        conn = sqlite3.connect('ctf.db')
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE username=? AND password=? AND registration_type='admin'", (username, password))
        admin = c.fetchone()
        
        if not admin:
            conn.close()
            return self.send_error_json(403, "Unauthorized")
            
        try:
            # Delete references
            c.execute("DELETE FROM submissions WHERE challenge_id=?", (challenge_id,))
            c.execute("DELETE FROM challenges WHERE id=?", (challenge_id,))
            conn.commit()
            self.send_success_json({"message": "Challenge deleted successfully!"})
        except Exception as e:
            self.send_error_json(400, str(e))
        finally:
            conn.close()

    def send_error_json(self, code, message):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'error': message}).encode())

    def send_success_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

if __name__ == '__main__':
    init_db()
    with socketserver.TCPServer(("", PORT), MyRequestHandler) as httpd:
        print(f"Serving at port {PORT}")
        httpd.serve_forever()
