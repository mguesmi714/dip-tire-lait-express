"""
Patch Playwright coreBundle.js pour corriger le crash Firefox
sur les erreurs JS sans location (Pages Jaunes).

A relancer apres : pip install --upgrade playwright
"""
import pathlib

def patch():
    import playwright
    p = pathlib.Path(playwright.__file__).parent / "driver" / "package" / "lib" / "coreBundle.js"

    if not p.exists():
        print(f"Fichier non trouve : {p}")
        return

    content = p.read_text(encoding="utf-8")

    old = """            location: {
              url: pageError.location.url,
              line: pageError.location.lineNumber,
              column: pageError.location.columnNumber
            }"""

    new = """            location: pageError.location ? {
              url: pageError.location.url,
              line: pageError.location.lineNumber,
              column: pageError.location.columnNumber
            } : { url: '', line: 0, column: 0 }"""

    if new in content:
        print("Patch deja applique.")
        return

    if old not in content:
        print("Pattern non trouve — le patch est peut-etre deja integre dans cette version.")
        return

    p.write_text(content.replace(old, new), encoding="utf-8")
    print("Patch applique avec succes.")

if __name__ == "__main__":
    patch()
