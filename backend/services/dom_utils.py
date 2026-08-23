import hashlib
from bs4 import BeautifulSoup

def normalize_dom(html_content: str) -> str:
    """
    Нормализует DOM для сравнения между шагами.
    Удаляет volatile-элементы (скрипты, стили) и атрибуты,
    которые часто меняются без реального изменения состояния страницы.
    """
    if not html_content:
        return ""
        
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. Удаляем невидимые и динамические теги
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "canvas"]):
        tag.decompose()
        
    # 2. Удаляем атрибуты, которые склонны меняться
    volatile_attrs = ['id', 'class', 'style', 'data-reactid', 'data-testid', 'datetime', 'data-timestamp']
    for tag in soup.find_all(True):
        for attr in volatile_attrs:
            if attr in tag.attrs:
                del tag[attr]
                
    # Получаем нормализованный текст (с тегами, но без левых атрибутов)
    # Используем prettify или просто str(), предварительно удалив лишние пробелы
    text = soup.prettify()
    
    # Можно еще сжать пробелы
    import re
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def calculate_dom_hash(html_content: str) -> str:
    """Вычисляет SHA-256 хэш нормализованного DOM."""
    normalized = normalize_dom(html_content)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
