from django.db import connections
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

def get_table_schema():
    with connections['externa_2012'].cursor() as cursor:
        cursor.execute("""
            SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'CigarRings2012'
        """)
        for row in cursor.fetchall():
            print(f"Columna: {row[0]} | Tipo: {row[1]} | Largo Max: {row[2]}")

if __name__ == "__main__":
    get_table_schema()
