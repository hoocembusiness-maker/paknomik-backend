from user.quote_forms import MAX_FILE_BYTES, MAX_FILES

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def clean_request_files(files) -> str:
    if len(files) > MAX_FILES:
        return f"{MAX_FILES} fichiers maximum par envoi."
    for upload in files:
        name = (upload.name or "").lower()
        if not any(name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            return "Formats acceptés : PDF, PNG, JPG, WEBP."
        if upload.size > MAX_FILE_BYTES:
            return "Chaque fichier doit faire moins de 8 Mo."
    return ""
