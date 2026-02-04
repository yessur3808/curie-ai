import socket
import subprocess
import sys
import os

def is_port_in_use(port):
    """Check if a port is in use on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def get_postgres_process():
    """Return system Postgres process info if running, else None."""
    try:
        output = subprocess.check_output(['lsof', '-i', ':5432'], text=True)
        for line in output.splitlines():
            if 'postgres' in line:
                return line
    except subprocess.CalledProcessError:
        pass
    return None

def stop_postgres_service():
    """Attempt to stop system Postgres service."""
    print("Trying to stop system PostgreSQL service...")
    # Try both common service names
    for service_name in ["postgresql", "postgres"]:
        result = subprocess.run(['sudo', 'systemctl', 'stop', service_name])
        if result.returncode == 0:
            print(f"Stopped {service_name} successfully.")
            return True
    print("Failed to stop system PostgreSQL service. Please stop it manually.")
    return False

def main():
    print("Checking if port 5432 (Postgres) is in use...")
    if is_port_in_use(5432):
        print("Port 5432 is IN USE.")
        pg_process = get_postgres_process()
        if pg_process:
            print("System PostgreSQL process detected:\n", pg_process)
            answer = input("Do you want to try to stop the system PostgreSQL service? [y/N]: ").strip().lower()
            if answer == 'y':
                if not stop_postgres_service():
                    print("Could not stop system Postgres. You may need to manually stop it or change the Docker port.")
                    sys.exit(1)
                else:
                    print("Please re-run your Docker Compose command.")
                    sys.exit(0)
            else:
                print("You can change the port in your docker-compose.yml (e.g., 5433:5432) and update your app config.")
                sys.exit(1)
        else:
            print("Port 5432 is in use, but not by a 'postgres' process. Investigate further!")
            sys.exit(1)
    else:
        print("Port 5432 is free. You are good to go!")
        sys.exit(0)

if __name__ == "__main__":
    main()