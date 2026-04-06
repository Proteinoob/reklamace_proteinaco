from dotenv import load_dotenv
import os

load_dotenv()

class Settings:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./reklamace.db")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # Shoptet
    SHOPTET_API_BASE: str = os.getenv("SHOPTET_API_BASE", "https://api.myshoptet.com")
    SHOPTET_TOKEN_CZ: str = os.getenv("SHOPTET_TOKEN_CZ", "")

    # Zásilkovna
    ZASILKOVNA_API_KEY: str = os.getenv("ZASILKOVNA_API_KEY", "")
    ZASILKOVNA_SENDER_ID: str = os.getenv("ZASILKOVNA_SENDER_ID", "")

    # SMTP
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
    EMAIL_FROM_NAME: str = os.getenv("EMAIL_FROM_NAME", "Proteinaco")

    # Admin
    ADMIN_PORTAL_URL: str = os.getenv("ADMIN_PORTAL_URL", "http://localhost:8000")

    # Uploads
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", "./uploads/complaints")

    # Rate limiting
    RATE_LIMIT_LOOKUP: int = int(os.getenv("RATE_LIMIT_LOOKUP", "10"))
    RATE_LIMIT_CREATE: int = int(os.getenv("RATE_LIMIT_CREATE", "5"))

settings = Settings()
