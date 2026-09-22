from app import app, socketio, start_background_threads

start_background_threads()

if __name__ == "__main__":
    socketio.run(app)
