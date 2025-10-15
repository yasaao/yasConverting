import os
import zipfile
import io
import mimetypes
import uuid
import time
import threading 
from flask import Flask, render_template, request, jsonify, send_file, session
from PIL import Image

# --------------------------
# KONFIGURASI & STORAGE APLIKASI
# --------------------------
app = Flask(__name__)
# Kunci rahasia wajib
app.config['SECRET_KEY'] = 'kunci_rahasia_untuk_sesi_aman_dan_polling_98765' 
app.config['PERMANENT_SESSION_LIFETIME'] = 3600

# Storage In-Memory: Data file sementara yang diupload
TEMP_STORAGE = {} 

# Status Job Polling: Menyimpan status konversi real-time per job
JOB_STATUS = {} 

MIMETYPE_MAP = {
    'png': 'image/png',
    'bmp': 'image/bmp',
    'tga': 'image/x-tga'
}

# --------------------------
# FUNGSI UTILITAS KONVERSI
# --------------------------
def convert_image(img_stream, target_format):
    """Mengonversi stream gambar (BytesIO) ke format target."""
    try:
        img = Image.open(img_stream)
        output_stream = io.BytesIO()
        save_format = target_format.upper()
        
        # --- LOGIKA KONVERSI DENGAN PENGECEKAN MODE GAMBAR ---
        if save_format == 'PNG':
            # PNG: memastikan mode RGBA jika ada transparansi
            if 'A' in img.mode: 
                img = img.convert('RGBA')
            elif img.mode == 'P' and 'transparency' in img.info:
                img = img.convert('RGBA')
            elif img.mode != 'RGB':
                 img = img.convert('RGB')
            img.save(output_stream, 'PNG')
            
        elif save_format == 'BMP':
            # BMP: harus dikonversi ke mode RGB, lalu dikuantisasi ke P/8-bit jika diinginkan (default RGB)
            if img.mode not in ('RGB', 'L'):
                img = img.convert('RGB')
            # Jika BMP terlalu besar atau kompleks, coba konversi ke mode P (paletted/8-bit)
            # img = img.quantize(256) 
            img.save(output_stream, 'BMP')

        elif save_format == 'TGA':
            # TGA: Mendukung RGB atau RGBA
            if 'A' in img.mode:
                 img = img.convert('RGBA')
            elif img.mode != 'RGB':
                 img = img.convert('RGB')
            img.save(output_stream, 'TGA', rle=True)
            
        output_stream.seek(0)
        return output_stream

    except Exception as e:
        # PENTING: Mencetak error spesifik
        print(f"!!! Error Konversi: Format {target_format} gagal. Pesan: {e}")
        return None


# --------------------------
# FUNGSI THREAD LATAR BELAKANG
# --------------------------
def run_conversion_in_background(job_id, file_ids, target_format):
    """Fungsi yang berjalan di thread terpisah untuk melakukan konversi."""
    total_files = len(file_ids)
    files_completed = 0
    
    JOB_STATUS[job_id] = {
        'status': 'running',
        'progress': 0, 
        'results': [] 
    }

    for file_id in file_ids:
        
        current_progress = int((files_completed / total_files) * 100)
        JOB_STATUS[job_id]['progress'] = current_progress
        
        file_info = TEMP_STORAGE.get(file_id)
        if not file_info:
            JOB_STATUS[job_id]['results'].append({'file_id': file_id, 'status': 'error', 'message': 'File hilang dari memori server'})
            files_completed += 1
            continue
            
        original_filename = file_info['filename']
        file_data = file_info['data']
        original_filename_root, original_extension = os.path.splitext(original_filename)
        is_zip = original_extension.lower() == '.zip'
        
        output_filename = ""
        try:
            # --- LOGIKA KONVERSI ZIP ATAU FILE TUNGGAL ---
            if is_zip:
                # Logika Konversi ZIP... (tidak diubah dari versi sebelumnya)
                zip_input_stream = io.BytesIO(file_data)
                zip_output_stream = io.BytesIO()
                
                with zipfile.ZipFile(zip_input_stream, 'r') as zip_in:
                    with zipfile.ZipFile(zip_output_stream, 'w', zipfile.ZIP_DEFLATED) as zip_out:
                        for filename in zip_in.namelist():
                            mime_type, _ = mimetypes.guess_type(filename)
                            if mime_type and mime_type.startswith('image/'):
                                image_data = zip_in.read(filename)
                                converted_stream = convert_image(io.BytesIO(image_data), target_format)
                                if converted_stream:
                                    new_filename = os.path.splitext(os.path.basename(filename))[0] + '.' + target_format
                                    zip_out.writestr(new_filename, converted_stream.getvalue())
                                else:
                                    # Tambahkan pesan error jika konversi di dalam ZIP gagal
                                    print(f"Konversi file dalam ZIP gagal: {filename}")
                                    
                zip_output_stream.seek(0)
                output_filename = f"{original_filename_root}_yasConvert!_{target_format}.zip"
                
                TEMP_STORAGE[file_id]['converted_data'] = zip_output_stream.getvalue()
                TEMP_STORAGE[file_id]['download_name'] = output_filename
                TEMP_STORAGE[file_id]['converted_mime'] = 'application/zip'
                
            else:
                # Logika Konversi File Tunggal...
                converted_stream = convert_image(io.BytesIO(file_data), target_format)

                if not converted_stream:
                    raise Exception("Gagal mengonversi gambar. Lihat console server untuk detail.")
                    
                output_filename = f"{original_filename_root}_converted.{target_format}"
                
                TEMP_STORAGE[file_id]['converted_data'] = converted_stream.getvalue()
                TEMP_STORAGE[file_id]['download_name'] = output_filename
                TEMP_STORAGE[file_id]['converted_mime'] = MIMETYPE_MAP.get(target_format, 'application/octet-stream')

            # Berhasil
            TEMP_STORAGE[file_id]['status'] = 'completed'
            JOB_STATUS[job_id]['results'].append({'file_id': file_id, 'status': 'completed', 'download_name': output_filename})

        except Exception as e:
            # Gagal
            print(f"!!! Error Fatal Saat Memproses {original_filename}: {e}")
            TEMP_STORAGE[file_id]['status'] = 'error'
            JOB_STATUS[job_id]['results'].append({'file_id': file_id, 'status': 'error', 'message': f"Error: {e}"})

        files_completed += 1
        
    # Final Update status
    JOB_STATUS[job_id]['progress'] = 100
    JOB_STATUS[job_id]['status'] = 'completed'


# --------------------------
# ROUTES FLASK (TIDAK ADA PERUBAHAN SIGNIFIKAN DI ROUTE INI)
# --------------------------

@app.route('/', methods=['GET'])
def index():
    session.pop('uploaded_files', None)
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    """Menerima file tunggal via AJAX."""
    if 'file' not in request.files or request.files['file'].filename == '':
        return jsonify({'success': False, 'message': 'Tidak ada file dipilih'}), 400
    
    uploaded_file = request.files['file']
    file_id = str(uuid.uuid4())
    
    try:
        # Coba membaca file. Jika terlalu besar, ini mungkin gagal di beberapa server.
        file_bytes = uploaded_file.read()
    except Exception as e:
        # Jika gagal membaca file (misalnya batas ukuran server terlampaui)
        print(f"!!! GAGAL MEMBACA FILE DARI REQUEST: {e}")
        return jsonify({'success': False, 'message': f'File terlalu besar atau error server saat upload: {e}'}), 500

    # Simpan data file di memori
    TEMP_STORAGE[file_id] = {
        'filename': uploaded_file.filename,
        'data': file_bytes,
        'mime': uploaded_file.mimetype,
        'status': 'uploaded'
    }
    
    if 'uploaded_files' not in session:
        session['uploaded_files'] = []
    
    session['uploaded_files'].append(file_id)
    session.modified = True
    
    return jsonify({
        'success': True, 
        'file_id': file_id, 
        'filename': uploaded_file.filename
    })

@app.route('/remove/<file_id>', methods=['POST'])
def remove_file(file_id):
    if file_id in TEMP_STORAGE:
        del TEMP_STORAGE[file_id]
        if 'uploaded_files' in session and file_id in session['uploaded_files']:
            session['uploaded_files'].remove(file_id)
            session.modified = True
        return jsonify({'success': True})
    return jsonify({'success': False, 'message': 'File tidak ditemukan'}), 404

@app.route('/start_conversion', methods=['POST'])
def start_conversion():
    data = request.get_json()
    target_format = data.get('format', 'png').lower()
    file_ids = data.get('file_ids', [])

    if not file_ids:
        return jsonify({'success': False, 'message': 'Tidak ada file untuk dikonversi'}), 400

    job_id = str(uuid.uuid4())
    
    # Jalankan proses konversi di thread terpisah
    thread = threading.Thread(
        target=run_conversion_in_background, 
        args=(job_id, file_ids, target_format)
    )
    thread.start()

    return jsonify({'success': True, 'job_id': job_id})

@app.route('/get_conversion_status/<job_id>', methods=['GET'])
def get_conversion_status(job_id):
    if job_id not in JOB_STATUS:
        return jsonify({'status': 'completed', 'progress': 100, 'results': []})
    
    status_data = JOB_STATUS[job_id]
    
    if status_data['status'] in ['completed', 'error']:
        response_data = status_data.copy()
        JOB_STATUS.pop(job_id, None)
        return jsonify(response_data)
    
    return jsonify(status_data)


@app.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    file_info = TEMP_STORAGE.get(file_id)
    
    if not file_info or 'converted_data' not in file_info:
        return "File konversi tidak ditemukan atau belum selesai.", 404
    
    return send_file(
        io.BytesIO(file_info['converted_data']),
        mimetype=file_info['converted_mime'],
        as_attachment=True,
        download_name=file_info['download_name']
    )


if __name__ == '__main__':
    # Gunakan threaded=True
    app.run(debug=True, threaded=True)