# app.py
from flask import Flask, render_template, request, redirect, url_for, session, Response, abort
import cv2
import os
import threading
import queue
from dotenv import load_dotenv
import time
import logging

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', os.urandom(24).hex())

LOGIN = os.getenv('LOGIN')
PASSWORD = os.getenv('PASSWORD')

# Настройка логирования (INFO для Docker)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Глобальная переменная для кеширования error_frame
error_frame = None
ERROR_IMAGE_PATH = os.path.join(app.static_folder or 'static', 'img', 'unavailable.jpg')

# Функция для загрузки error_frame (вызывается один раз)
def load_error_frame():
    global error_frame
    if error_frame is None:
        try:
            img_path = ERROR_IMAGE_PATH
            if os.path.exists(img_path):
                error_frame = cv2.imread(img_path)
            if error_frame is None:
                # Fallback: создаем текстовый фрейм
                import numpy as np
                error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(error_frame, 'Камера недоступна', (150, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        except Exception as e:
            logging.error(f"Error loading error_frame: {e}")
            # Ultimate fallback
            import numpy as np
            error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(error_frame, 'Ошибка', (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
    return error_frame

# Глобальные словари для потоков и очередей
capture_threads = {}
frame_queues = {}

def start_capture_thread(stream_id, rtsp_url):
    """Запускает поток для чтения кадров из RTSP и помещения их в очередь."""
    load_error_frame()
    
    def capture_loop():
        try:
            # Force UDP transport and increase timeout
            os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp|timeout;60000000|stimeout;60000000'
            
            logging.info(f"Attempting to open RTSP for {stream_id}: {rtsp_url}")
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)
            
            if not cap.isOpened():
                logging.error(f"Failed to open RTSP for {stream_id}: {rtsp_url}")
                # Fallback loop: put error_frame
                while True:
                    try:
                        frame_queues[stream_id].put(error_frame, block=False)
                        time.sleep(1/30)
                    except (queue.Full, KeyError):
                        if stream_id in frame_queues:
                            frame_queues[stream_id].get()
                        time.sleep(1/30)
                return
            
            logging.info(f"RTSP opened successfully for {stream_id}")
            
            while True:
                success, frame = cap.read()
                if not success:
                    frame = error_frame
                try:
                    frame_queues[stream_id].put(frame, block=False)
                    time.sleep(1/30)  # ~30 FPS
                except (queue.Full, KeyError):
                    if stream_id in frame_queues:
                        frame_queues[stream_id].get()
                    time.sleep(1/30)
        except Exception as e:
            logging.error(f"Exception in capture_loop for {stream_id}: {e}")
        finally:
            if 'cap' in locals():
                cap.release()
    
    # Создаём очередь если нет
    if stream_id not in frame_queues:
        frame_queues[stream_id] = queue.Queue(maxsize=10)
    # Стартуем поток
    thread = threading.Thread(target=capture_loop, daemon=True)
    thread.start()
    capture_threads[stream_id] = thread
    logging.info(f"Capture thread started for {stream_id}")

# Функция для генерации MJPEG-стрима из очереди
def gen_frames(stream_id):
    load_error_frame()
    while True:
        try:
            # Lazy init queue if missing
            if stream_id not in frame_queues:
                frame_queues[stream_id] = queue.Queue(maxsize=10)
                start_capture_thread(stream_id, RTSP_URLS[stream_id])  # Start on demand
            
            frame = frame_queues[stream_id].get(timeout=1)
        except (queue.Empty, KeyError) as e:
            logging.warning(f"Queue issue for {stream_id}: {e}")
            frame = error_frame
        except Exception as e:
            logging.error(f"Error in gen_frames for {stream_id}: {e}")
            frame = error_frame
        
        try:
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ret:
                logging.error(f"Failed to encode frame for {stream_id}")
                frame = error_frame
                continue
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
        except Exception as e:
            logging.error(f"Encode error for {stream_id}: {e}")
            # Yield error frame as fallback
            ret, buffer = cv2.imencode('.jpg', error_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# RTSP URL'ы из .env
RTSP_URLS = {
    'stream1': os.getenv('RTSP_STREAM1'),
    'stream2': os.getenv('RTSP_STREAM2'),
    'stream3': os.getenv('RTSP_STREAM3')
}

# Инициализация стримов
def init_streams():
    for stream_id, rtsp_url in RTSP_URLS.items():
        if rtsp_url:  # Skip if empty
            start_capture_thread(stream_id, rtsp_url)
    logging.info("Все RTSP-потоки запущены параллельно.")

@app.before_first_request
def before_first_request():
    init_streams()  # Init in worker for Gunicorn/Docker

@app.route('/')
def index():
    return redirect(url_for('video'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('email')
        password = request.form.get('password')
        if username == LOGIN and password == PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('video'))
        else:
            return render_template('login.html', error='Неверный логин или пароль')
    return render_template('login.html')

@app.route('/video')
def video():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template('video.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# Роут для стриминга видео
@app.route('/<stream_id>.mjpg')
def video_stream(stream_id):
    if not session.get('logged_in'):
        abort(401)
    if stream_id not in RTSP_URLS:
        logging.error(f"Invalid stream_id: {stream_id}")
        abort(404)
    
    try:
        return Response(gen_frames(stream_id), mimetype='multipart/x-mixed-replace; boundary=frame')
    except Exception as e:
        logging.error(f"Error in video_stream for {stream_id}: {e}")
        # Fallback: Single error image response
        load_error_frame()
        ret, buffer = cv2.imencode('.jpg', error_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return Response(buffer.tobytes(), mimetype='image/jpeg')

# Обработка 404
@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

# Для Gunicorn post_fork (если нужно)
def post_fork(server, worker):
    init_streams()

# Для локального запуска
if __name__ == '__main__':
    init_streams()
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)