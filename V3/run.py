import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    # 5003 para no chocar con la V1/V2 (5000); `PORT` lo cambia (ej. en un servidor).
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5003)))
