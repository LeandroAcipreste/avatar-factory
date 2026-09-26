def recommendations(duration: float | None, width: int | None, height: int | None, size_bytes: int) -> list[str]:
    notes: list[str] = []
    if duration is None:
        notes.append("Não foi possível determinar a duração; envie um vídeo compatível.")
    elif duration < 10:
        notes.append("Prefira um vídeo de pelo menos 10 segundos com a pessoa falando para melhor cobertura futura.")
    elif duration > 180:
        notes.append("O vídeo é longo; um recorte de 30 a 120 segundos pode acelerar uma futura etapa de treino.")
    if not width or not height or min(width, height) < 720:
        notes.append("Use resolução mínima de 720p, boa iluminação e o rosto visível para melhor qualidade.")
    if size_bytes < 1_000_000:
        notes.append("O arquivo é pequeno; confirme se a imagem não está excessivamente comprimida.")
    if not notes:
        notes.append("Qualidade básica adequada. A geração real depende de um conector futuro autorizado.")
    return notes
