# scripts/seed_promos.py
from promos_db import promo_db

def main():
    codes_300 = promo_db.generate_many(amount=300, n=10, prefix="KL300")
    codes_600 = promo_db.generate_many(amount=600, n=10, prefix="KL600")
    codes_900 = promo_db.generate_many(amount=900, n=10, prefix="KL900")

    print("\n=== ПРОМОКОДЫ НА 300 ₽ ===")
    for c in codes_300:
        print(c)

    print("\n=== ПРОМОКОДЫ НА 600 ₽ ===")
    for c in codes_600:
        print(c)

    print("\n=== ПРОМОКОДЫ НА 900 ₽ ===")
    for c in codes_900:
        print(c)

if __name__ == "__main__":
    main()
