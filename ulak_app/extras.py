"""Optional weather, a deterministic daily puzzle, and publisher illustrations."""
from datetime import datetime, timedelta
import io
import json
import random
from urllib.parse import urlencode, urlparse

from PIL import Image, ImageOps

from .feeds import read_url
from .i18n import get_locale


def weather(config, now):
    settings = config["weather"]
    if not settings["enabled"]:
        return None
    query = urlencode(dict(latitude=settings["latitude"], longitude=settings["longitude"],
        current="temperature_2m,weather_code,wind_speed_10m", timezone=config["timezone"]))
    data = json.loads(read_url("https://api.open-meteo.com/v1/forecast?"+query, timeout=10))
    current = data["current"]
    stamp = datetime.fromisoformat(current["time"]).replace(tzinfo=now.tzinfo)
    if abs(now-stamp) > timedelta(hours=3):
        raise ValueError("Weather report is stale")
    temp, wind = current["temperature_2m"], current["wind_speed_10m"]
    if type(temp) not in (int, float) or type(wind) not in (int, float):
        raise ValueError("Weather report has no temperature/wind")
    code = current["weather_code"]
    kind = 0 if code==0 else 1 if code<=3 else 2 if code<=48 else 3 if code<=67 else 4 if code<=77 else 3 if code<=82 else 4 if code<=86 else 5
    labels = get_locale(config['language'])['weather_conditions']
    return dict(label=settings["label"], temperature=temp, wind=wind,
                condition=labels[kind], observed=current["time"],
                source="Open-Meteo", url="https://open-meteo.com/")


def solutions(grid, limit=2):
    for row in range(4):
        for col in range(4):
            if grid[row][col] == 0:
                count = 0
                used = set(grid[row]) | {grid[r][col] for r in range(4)}
                used |= {grid[r][c] for r in range(row//2*2,row//2*2+2) for c in range(col//2*2,col//2*2+2)}
                for value in set(range(1,5))-used:
                    grid[row][col] = value
                    count += solutions(grid, limit-count)
                    if count >= limit:
                        break
                grid[row][col] = 0
                return count
    return 1


def puzzle(now):
    rng = random.Random(now.date().isoformat())
    digits = list(range(1,5))
    rng.shuffle(digits)
    solution = [[digits[(r*2+r//2+c)%4] for c in range(4)] for r in range(4)]
    grid = [row[:] for row in solution]
    positions = list(range(16))
    rng.shuffle(positions)
    removed = 0
    for position in positions:
        row, col = divmod(position,4)
        value, grid[row][col] = grid[row][col], 0
        if solutions([r[:] for r in grid]) != 1:
            grid[row][col] = value
        else:
            removed += 1
        if removed >= 8:
            break
    return dict(type="sudoku4", grid=grid, solution=solution)


def daily_puzzle(now,config):
    kind=config['puzzle']['type']
    if kind=='rotate': kind='anagram' if now.date().toordinal()%2 else 'sudoku4'
    if kind=='sudoku4': return puzzle(now)
    words=get_locale(config['language'])['anagram_words']
    rng=random.Random(now.date().isoformat())
    answer=rng.choice(words);letters=list(answer)
    rng.shuffle(letters)
    if ''.join(letters)==answer: letters=letters[1:]+letters[:1]
    return dict(type='anagram',letters=' '.join(letters),solution=answer)


def concept(now,language):
    concepts=get_locale(language)['concepts']
    selected=concepts[now.date().toordinal()%len(concepts)]
    return dict(selected,kind='original_editorial_reference')


def illustration(url, grayscale=True):
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Image URL must use HTTP(S)")
    data = read_url(url, timeout=8, limit=3_000_000)
    with Image.open(io.BytesIO(data)) as original:
        if original.width*original.height > 20_000_000:
            raise ValueError("Image is too large")
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((600,400))
    if grayscale:
        image = ImageOps.grayscale(image)
    return image


def ascii_image(image, columns=40, rows=13):
    image = ImageOps.grayscale(image).resize((columns,rows))
    chars = "@%#*+=-:. "
    pixels = list(image.get_flattened_data() if hasattr(image, 'get_flattened_data') else image.getdata())
    return [''.join(chars[min(9,pixels[r*columns+c]*10//256)] for c in range(columns)) for r in range(rows)]
