import sys
import numpy as np
import cv2
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QFileDialog, QSizePolicy, QComboBox, QDoubleSpinBox, QFormLayout)
from PyQt5.QtGui import QImage, QPixmap, QPainter
from PyQt5.QtCore import QTimer, Qt, QThread, pyqtSignal
import os
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
class ImageLabel(QLabel):
    def __init__(self, text=""):
        super().__init__(text)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: black; color: white;")
        self.setMinimumSize(256, 256)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pixmap = None

    def set_custom_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def paintEvent(self, event):
        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter = QPainter(self)
            painter.drawPixmap(x, y, scaled)
        else:
            super().paintEvent(event)

class InteractiveGraph(FigureCanvas):
    def __init__(self, parent=None, width=5, height=4, dpi=100):
        self.fig, self.ax = plt.subplots(figsize=(width, height), dpi=dpi)
        super().__init__(self.fig)
        self.setParent(parent)
        
        # Initial control points for the curve
        self.points = np.array([[0.0, 0.0], [1.0, 1.0]])
        self.line, = self.ax.plot(self.points[:, 0], self.points[:, 1], 'bo-', markersize=8)
        
        self.ax.set_xlim(-0.05, 1.05)
        self.ax.set_ylim(-0.05, 1.05)
        self.ax.set_title("Animation Progress Curve")
        self.ax.set_xlabel("Time (0 to 1)")
        self.ax.set_ylabel("Progress (Path)")
        self.ax.grid(True)
        
        self.dragged_point = None
        self.mpl_connect('button_press_event', self.on_press)
        self.mpl_connect('button_release_event', self.on_release)
        self.mpl_connect('motion_notify_event', self.on_motion)
        
        # Add points button/state could go here
    
    def on_press(self, event):
        if event.inaxes != self.ax: return
        
        # Don't add points too close to ends or outside valid bounds
        x = event.xdata
        y = event.ydata
        if x is None or y is None: return
        
        # Find closest point
        distances = np.sqrt((self.points[:, 0] - x)**2 + (self.points[:, 1] - y)**2)
        min_idx = np.argmin(distances)
        
        if distances[min_idx] < 0.05:
            # Clicked near an existing point, drag it
            self.dragged_point = min_idx
        else:
            # Clicked somewhere else, add a new point
            # Only add if x is reasonably within 0-1 bounds
            if 0.02 < x < 0.98:
                self.add_point(x, y)
                # Automatically start dragging the newly created point
                idx = np.searchsorted(self.points[:, 0], x)
                # Due to float precision, we just find the closest point again which will be the one we just added
                distances = np.sqrt((self.points[:, 0] - x)**2 + (self.points[:, 1] - y)**2)
                self.dragged_point = np.argmin(distances)
            
    def on_release(self, event):
        self.dragged_point = None
        
    def on_motion(self, event):
        if self.dragged_point is None or event.inaxes != self.ax: return
        
        # Don't move the endpoints x values to keep them at 0 and 1
        x = event.xdata
        y = np.clip(event.ydata, -0.2, 1.2) # clip y but allow going a bit past bounds
        
        if self.dragged_point == 0:
            x = 0.0
            y = 0.0
        elif self.dragged_point == len(self.points) - 1:
            x = 1.0
            y = 1.0
        else:
            x = np.clip(x, self.points[self.dragged_point-1, 0] + 0.05, self.points[self.dragged_point+1, 0] - 0.05)
            
        self.points[self.dragged_point] = [x, y]
        self.update_plot()
        
    def add_point(self, x, y):
        idx = np.searchsorted(self.points[:, 0], x)
        self.points = np.insert(self.points, idx, [x, y], axis=0)
        self.update_plot()
        
    def update_plot(self):
        self.line.set_data(self.points[:, 0], self.points[:, 1])
        self.draw()
        
    def get_progress(self, time_val):
        """Returns the interpolated Y value at given X (time)"""
        if len(self.points) < 2: return time_val
        f = interp1d(self.points[:, 0], self.points[:, 1], kind='linear', bounds_error=False, fill_value=(0, 1))
        return float(f(time_val))

class PixelSorterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Live Pixel Sorter Animation")
        self.resize(1600, 900)
        
        self.img_source = None
        self.img_target = None
        self.src_filename = "source"
        self.tgt_filename = "target"
        self.original_positions = None # (N, 2)
        self.target_positions = None   # (N, 2)
        self.pixels = None             # (N, 3)
        self.canvas_shape = None       # (H, W, 3)
        
        self.elapsed_time = 0.0
        self.timer_interval = 33 # roughly 30 fps
        self.dt = self.timer_interval / 1000.0
        
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_frame)
        self.playing = False
        self.recording = False
        
        if not os.path.exists("results"):
            os.makedirs("results")
            
        self.init_ui()
        
    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        
        # Left side: Image and Controls
        left_layout = QVBoxLayout()
        
        self.image_label = ImageLabel("Load Source and Target images to start")
        left_layout.addWidget(self.image_label)
        
        self.status_label = QLabel("")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 14px;")
        left_layout.addWidget(self.status_label)
        
        # Settings Layout
        settings_layout = QFormLayout()
        
        self.spin_duration = QDoubleSpinBox()
        self.spin_duration.setRange(0.1, 60.0)
        self.spin_duration.setValue(3.0)
        self.spin_duration.setSingleStep(0.5)
        settings_layout.addRow("Animation Duration (s):", self.spin_duration)
        
        self.spin_start_pause = QDoubleSpinBox()
        self.spin_start_pause.setRange(0.0, 10.0)
        self.spin_start_pause.setValue(0.2)
        self.spin_start_pause.setSingleStep(0.1)
        settings_layout.addRow("Start Pause (s):", self.spin_start_pause)
        
        self.spin_end_pause = QDoubleSpinBox()
        self.spin_end_pause.setRange(0.0, 10.0)
        self.spin_end_pause.setValue(0.2)
        self.spin_end_pause.setSingleStep(0.1)
        settings_layout.addRow("End Pause (s):", self.spin_end_pause)
        
        left_layout.addLayout(settings_layout)
        
        controls_layout = QHBoxLayout()
        
        btn_load_src = QPushButton("Load Source")
        btn_load_src.clicked.connect(self.load_source_image)
        controls_layout.addWidget(btn_load_src)
        
        btn_load_tgt = QPushButton("Load Target")
        btn_load_tgt.clicked.connect(self.load_target_image)
        controls_layout.addWidget(btn_load_tgt)
        
        self.btn_play = QPushButton("Play Animation")
        self.btn_play.clicked.connect(self.toggle_animation)
        self.btn_play.setEnabled(False)
        controls_layout.addWidget(self.btn_play)
        
        btn_reset = QPushButton("Reset Time")
        btn_reset.clicked.connect(self.reset_animation)
        btn_reset.setEnabled(False)
        self.btn_reset = btn_reset
        controls_layout.addWidget(btn_reset)
        
        left_layout.addLayout(controls_layout)
        
        # Recording & Export Controls
        export_layout = QHBoxLayout()
        
        btn_export = QPushButton("Export Current Image")
        btn_export.clicked.connect(self.export_image)
        btn_export.setEnabled(False)
        self.btn_export = btn_export
        export_layout.addWidget(btn_export)
        
        self.combo_format = QComboBox()
        self.combo_format.addItems([".mp4"])
        export_layout.addWidget(self.combo_format)
        
        btn_record = QPushButton("Record Animation")
        btn_record.clicked.connect(self.record_animation)
        btn_record.setEnabled(False)
        self.btn_record = btn_record
        export_layout.addWidget(btn_record)
        
        left_layout.addLayout(export_layout)
        
        # Right side: Graph
        right_layout = QVBoxLayout()
        self.graph = InteractiveGraph(self)
        right_layout.addWidget(self.graph)
        
        thumbs_layout = QHBoxLayout()
        self.thumb_src = ImageLabel("Source")
        self.thumb_src.setMinimumSize(128, 128)
        self.thumb_tgt = ImageLabel("Target")
        self.thumb_tgt.setMinimumSize(128, 128)
        thumbs_layout.addWidget(self.thumb_src)
        thumbs_layout.addWidget(self.thumb_tgt)
        
        right_layout.addLayout(thumbs_layout)
        
        main_layout.addLayout(left_layout, stretch=1)
        main_layout.addLayout(right_layout, stretch=1)
        
    def load_source_image(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Open Source Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp)")
        if not filename: return
        self.src_filename = os.path.splitext(os.path.basename(filename))[0]
        self.img_source = self._load_and_resize(filename)
        self._display_thumbnail(self.img_source, self.thumb_src)
        self._check_ready()
        
    def load_target_image(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Open Target Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp)")
        if not filename: return
        self.tgt_filename = os.path.splitext(os.path.basename(filename))[0]
        self.img_target = self._load_and_resize(filename)
        self._display_thumbnail(self.img_target, self.thumb_tgt)
        self._check_ready()
        
    def _display_thumbnail(self, img, label):
        h, w, ch = img.shape
        bytes_per_line = ch * w
        q_img = QImage(img.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        label.set_custom_pixmap(QPixmap.fromImage(q_img))
        
    def _load_and_resize(self, filename):
        img = cv2.imread(filename)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        h, w = img.shape[:2]
        # We increase the max dimension from 256 to 1920 (1080p limit) so the export retains high quality!
        max_dim = 1920
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        return img
        
    def _check_ready(self):
        if self.img_source is not None and self.img_target is not None:
            self.status_label.setText("Processing...")
            self.status_label.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 14px;")
            QApplication.processEvents() # Force UI update before heavy computation
            self.prepare_animation_data()
            self.reset_animation()
            self.btn_play.setEnabled(True)
            self.btn_reset.setEnabled(True)
            self.btn_export.setEnabled(True)
            self.btn_record.setEnabled(True)
            self.status_label.setText("")
            self.render_frame(self._get_progress_time(self.elapsed_time))
            
    def _get_progress_time(self, t_sec):
        duration = self.spin_duration.value()
        start_pause = self.spin_start_pause.value()
        
        if t_sec <= start_pause:
            return 0.0
        elif t_sec >= start_pause + duration:
            return 1.0
        else:
            return (t_sec - start_pause) / duration
            
    def prepare_animation_data(self):
        # Resize source to match target exactly so they have identical N pixels
        th, tw = self.img_target.shape[:2]
        self.img_source = cv2.resize(self.img_source, (tw, th))
        
        self.canvas_shape = self.img_target.shape
        
        # 1. Extraction
        y_coords, x_coords = np.indices((th, tw))
        src_positions = np.column_stack((x_coords.flatten(), y_coords.flatten())).astype(np.float32)
        tgt_positions = np.column_stack((x_coords.flatten(), y_coords.flatten())).astype(np.float32)
        
        src_pixels = self.img_source.reshape(-1, 3)
        tgt_pixels = self.img_target.reshape(-1, 3)
        
        # 2. Calculate Luminance (L = 0.299*R + 0.587*G + 0.114*B)
        src_lum = 0.299 * src_pixels[:, 0] + 0.587 * src_pixels[:, 1] + 0.114 * src_pixels[:, 2]
        tgt_lum = 0.299 * tgt_pixels[:, 0] + 0.587 * tgt_pixels[:, 1] + 0.114 * tgt_pixels[:, 2]
        
        # 3. Sort by Luminance
        src_sorted_indices = np.argsort(src_lum)
        tgt_sorted_indices = np.argsort(tgt_lum)
        
        # The ith pixel in src_sorted gets mapped to the ith position in tgt_sorted
        self.pixels = src_pixels[src_sorted_indices]
        self.original_positions = src_positions[src_sorted_indices]
        self.target_positions = tgt_positions[tgt_sorted_indices]
        
    def toggle_animation(self):
        total_time = self.spin_start_pause.value() + self.spin_duration.value() + self.spin_end_pause.value()
        if self.playing:
            self.timer.stop()
            self.btn_play.setText("Play Animation")
            self.playing = False
        else:
            if self.elapsed_time >= total_time:
                self.elapsed_time = 0.0
            self.timer.start(self.timer_interval)
            self.btn_play.setText("Pause Animation")
            self.playing = True
            
    def reset_animation(self):
        self.elapsed_time = 0.0
        self.playing = False
        self.timer.stop()
        self.btn_play.setText("Play Animation")
        self.render_frame(self._get_progress_time(self.elapsed_time))
        
    def update_frame(self):
        self.elapsed_time += self.dt
        total_time = self.spin_start_pause.value() + self.spin_duration.value() + self.spin_end_pause.value()
        
        if self.elapsed_time >= total_time:
            self.elapsed_time = total_time
            self.timer.stop()
            self.playing = False
            self.btn_play.setText("Play Animation")
            
        self.render_frame(self._get_progress_time(self.elapsed_time))
        
    def render_frame(self, time_val):
        if self.original_positions is None: return
        
        progress = self.graph.get_progress(time_val)
        
        # Interpolate positions
        current_positions = self.original_positions + (self.target_positions - self.original_positions) * progress
        current_positions = np.clip(np.round(current_positions), 0, [self.canvas_shape[1]-1, self.canvas_shape[0]-1]).astype(int)
        
        # Render onto blank canvas
        canvas = np.zeros(self.canvas_shape, dtype=np.uint8)
        
        # Vectorized assignment
        canvas[current_positions[:, 1], current_positions[:, 0]] = self.pixels
        
        # Update UI
        h, w, ch = canvas.shape
        bytes_per_line = ch * w
        q_img = QImage(canvas.data, w, h, bytes_per_line, QImage.Format_RGB888)
        self.image_label.set_custom_pixmap(QPixmap.fromImage(q_img))
        
        return canvas

    def get_export_name_base(self):
        return f"{self.src_filename}_to_{self.tgt_filename}"

    def _reset_status_text(self):
        self.status_label.setText("")

    def export_image(self):
        if self.original_positions is None: return
        canvas = self.render_frame(self._get_progress_time(self.elapsed_time))
        if canvas is not None:
            try:
                # Convert RGB back to BGR for cv2 saving
                img_bgr = cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR)
                file_name = f"{self.get_export_name_base()}_frame.png"
                path = os.path.join("results", file_name)
                cv2.imwrite(path, img_bgr)
                self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 14px;")
                self.status_label.setText(f"✅ SUCCESS: Image saved to results/{file_name}")
                QTimer.singleShot(5000, self._reset_status_text)
            except Exception as e:
                self.status_label.setStyleSheet("color: #F44336; font-weight: bold; font-size: 14px;")
                self.status_label.setText(f"❌ ERROR: Failed to save image: {str(e)}")
                QTimer.singleShot(5000, self._reset_status_text)

    def record_animation(self):
        if self.original_positions is None or self.recording: return
        self.recording = True
        self.btn_record.setText("Recording...")
        self.btn_record.setEnabled(False)
        self.btn_play.setEnabled(False)
        self.btn_reset.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.status_label.setStyleSheet("color: #FFC107; font-weight: bold; font-size: 14px;")
        self.status_label.setText("Rendering 30FPS Frames... Please wait.")
        QApplication.processEvents()
        
        fps = 30
        total_time = self.spin_start_pause.value() + self.spin_duration.value() + self.spin_end_pause.value()
        frames_count = int(total_time * fps)
        out_format = self.combo_format.currentText()
        file_name = f"{self.get_export_name_base()}_animation{out_format}"
        path = os.path.join("results", file_name)
        
        frames = []
        for i in range(frames_count + 1):
            t_sec = i / fps
            progress_time = self._get_progress_time(t_sec)
            frame = self.render_frame(progress_time)
            frames.append(frame)
            
            # Periodically process UI events so the application doesn't completely freeze or show "Not Responding"
            if i % 10 == 0:
                self.status_label.setText(f"Rendering frame {i}/{frames_count}...")
                QApplication.processEvents()
                
        self.status_label.setText("Saving file to disk...")
        QApplication.processEvents()
        
        try:
            # We use OpenCV's native VideoWriter for MP4
            h, w, _ = frames[0].shape
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(path, fourcc, fps, (w, h))
            for f in frames:
                # Convert back to BGR for OpenCV
                writer.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
            writer.release()
            
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 14px;")
            self.status_label.setText(f"✅ SUCCESS: Animation saved to {path}!")
            QTimer.singleShot(5000, self._reset_status_text)
        except Exception as e:
            self.status_label.setStyleSheet("color: #F44336; font-weight: bold; font-size: 14px;")
            self.status_label.setText(f"❌ ERROR: Failed to save {out_format}: {str(e)}")
            QTimer.singleShot(8000, self._reset_status_text)
            
        self.reset_animation()
        self.recording = False
        self.btn_record.setText("Record Animation")
        self.btn_record.setEnabled(True)
        self.btn_play.setEnabled(True)
        self.btn_reset.setEnabled(True)
        self.btn_export.setEnabled(True)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PixelSorterApp()
    window.show()
    sys.exit(app.exec_())
