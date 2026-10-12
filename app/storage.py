import os
import uuid

UPLOAD_DIR = "uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

async def save_uploaded_file(file):
    file_ext = file.filename.split('.')[-1] if '.' in file.filename else ''
    file_name = f"{uuid.uuid4()}.{file_ext}"
    file_path = os.path.join(UPLOAD_DIR, file_name)
    
    content = await file.read()
    with open(file_path, 'wb') as f:
        f.write(content)
    
    return file_path

def get_uploaded_file_path(relative_path):
    return os.path.abspath(relative_path)
