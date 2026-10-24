"""🎃 Halloween Party — бот конкурсу костюмів. Точка входу: `python bot.py` або start.bat.

Увесь код — у пакеті halloween_bot/ (див. README, розділ «Структура проєкту»).
"""
import sys

if sys.version_info < (3, 10):
    print(f"\n⛔ Потрібен Python 3.10 або новіший, а зараз запущено {sys.version.split()[0]}.\n"
          "   Завантаж свіжий з https://www.python.org/downloads/ (постав галочку «Add to PATH»),\n"
          "   видали папку .venv у проєкті й запусти start.bat ще раз.")
    try:
        input("\nНатисни Enter, щоб закрити…")
    except EOFError:
        pass
    sys.exit(1)

from halloween_bot.app import run  # noqa: E402  (імпорт після перевірки версії — навмисно)

if __name__ == "__main__":
    sys.exit(run())
