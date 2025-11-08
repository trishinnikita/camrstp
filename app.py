# app.py
from flask import Flask, render_template, request, redirect, url_for, session, Response, abort
import cv2
import os
import threading
import queue
from dotenv import load_dotenv
import time
import logging

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY')

LOGIN = os.getenv('LOGIN')
PASSWORD = os.getenv('PASSWORD')

# Настройка логирования
logging.basicConfig(level=logging.INFO)

logging.info(os.getenv('PYTHON_VERSION'))

# Глобальная переменная для кеширования error_frame
error_frame = None
ERROR_IMAGE_PATH = os.path.join(app.static_folder, 'img', 'unavailable.jpg')

# Функция для загрузки error_frame (вызывается один раз)
def load_error_frame():
    global error_frame
    if error_frame is None:
        # Пытаемся загрузить статическую картинку
        img_path = ERROR_IMAGE_PATH
        if os.path.exists(img_path):
            error_frame = cv2.imread(img_path)
            if error_frame is None:
                # Fallback: создаем текстовый фрейм, если файл поврежден
                import numpy as np
                error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(error_frame, 'Камера недоступна', (150, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        else:
            # Fallback: создаем текстовый фрейм, если файл не найден
            import numpy as np
            error_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(error_frame, 'Камера недоступна', (150, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
    return error_frame

# Глобальные словари для потоков и очередей
capture_threads = {}
frame_queues = {}

def start_capture_thread(stream_id, rtsp_url):
    """Запускает поток для чтения кадров из RTSP и помещения их в очередь."""
    load_error_frame()
    
    def capture_loop():
        # Force UDP transport and increase timeout (60 seconds in microseconds)
        os.environ['OPENCV_FFMPEG_CAPTURE_OPTIONS'] = 'rtsp_transport;udp|timeout;60000000'
        
        cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)  # Force FFMPEG backend
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 3)  # Буфер 3 кадра для снижения задержки
        
        if not cap.isOpened():
            logging.error(f"Failed to open RTSP for {stream_id}: {rtsp_url}")
            # Если не открылась, кладём error_frame в очередь
            while True:
                try:
                    frame_queues[stream_id].put(error_frame, block=False)
                    time.sleep(1/30)  # ~30 FPS
                except queue.Full:
                    frame_queues[stream_id].get()  # Очищаем очередь, если полна
            return
        
        logging.info(f"RTSP opened successfully for {stream_id}: {rtsp_url}")
        
        while True:
            success, frame = cap.read()
            if not success:
                frame = error_frame
            try:
                frame_queues[stream_id].put(frame, block=False)
                time.sleep(1/30)  # ~30 FPS
            except queue.Full:
                frame_queues[stream_id].get()  # Очищаем очередь, если полна
        cap.release()
    
    # Создаём очередь (maxsize=10 для буфера)
    frame_queues[stream_id] = queue.Queue(maxsize=10)
    # Стартуем поток
    thread = threading.Thread(target=capture_loop, daemon=True)
    thread.start()
    capture_threads[stream_id] = thread

# Функция для генерации MJPEG-стрима из очереди
def gen_frames(stream_id):
    while True:
        try:
            frame = frame_queues[stream_id].get(timeout=1)  # Ждём кадр max 1 сек
        except queue.Empty:
            frame = error_frame
        ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        frame_bytes = buffer.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

# RTSP URL'ы с правильным экранированием пароля (%40%40 для @@)
RTSP_URLS = {
    'stream1': os.getenv('RTSP_STREAM1'),
    'stream2': os.getenv('RTSP_STREAM2'),
    'stream3': os.getenv('RTSP_STREAM3')
}

# Инициализация стримов (для Gunicorn - вызывать в worker)
def init_streams():
    for stream_id, rtsp_url in RTSP_URLS.items():
        if stream_id not in frame_queues:
            start_capture_thread(stream_id, rtsp_url)
    logging.info("Все RTSP-потоки запущены параллельно.")

@app.route('/')
def index():
    return redirect(url_for('video'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('email')  # Соответствует name="email" в login.html
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

# Роут для стриминга видео (единый роут с переменной <stream_id>)
@app.route('/<stream_id>.mjpg')
def video_stream(stream_id):
    if not session.get('logged_in'):
        abort(401)  # Неавторизованные получают 401
    if stream_id not in RTSP_URLS:
        abort(404)
    return Response(gen_frames(stream_id), mimetype='multipart/x-mixed-replace; boundary=frame')

# Обработка 404
@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

# Для Gunicorn: Инициализация в worker после форка
def post_fork(server, worker):
    init_streams()

# Для локального запуска
if __name__ == '__main__':
    init_streams()
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)  # threaded=True для Flask