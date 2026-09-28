def recommendations(duration: float | None, width: int | None, height: int | None, size_bytes: int) -> list[str]:
    notes: list[str] = []
    if duration is None:
        notes.append("Não foi possível determinar a duração; envie um vídeo compatível.")
    elif duration < 10:
        notes.append("Prefira um vídeo de pelo menos 10 segundos com fala clara para preparar melhor um avatar.")
    elif duration > 180:
        notes.append("O vídeo é longo; um recorte de 30 a 120 segundos facilita a revisão técnica.")
    if not width or not height or min(width, height) < 720:
        notes.append("Use resolução mínima de 720p, boa iluminação e o rosto visível para melhor qualidade.")
    if size_bytes < 1_000_000:
        notes.append("O arquivo é pequeno; confirme se a imagem não está excessivamente comprimida.")
    if not notes:
        notes.append("Qualidade visual básica adequada para preparar o avatar.")
    return notes
