import pandas as pd
from django.db import connections
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

excel_path = r"C:\Users\Eugenio\OneDrive\Desktop\Cigar_rings\CR PLANNING.xlsm"

def find_missing_of():
    try:
        xls = pd.ExcelFile(excel_path, engine='openpyxl')
        sheet = 'STAMPING' if 'STAMPING' in xls.sheet_names else xls.sheet_names[0]
        
        df_preview = pd.read_excel(excel_path, engine='openpyxl', sheet_name=sheet, nrows=10, header=None)
        header_row = 0
        for index, row in df_preview.iterrows():
            if any(str(val).strip().upper() == "ORDEN" for val in row):
                header_row = index
                break
        
        df = pd.read_excel(excel_path, engine='openpyxl', sheet_name=sheet, header=header_row)
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Obtener todas las OFs del Excel
        ofs_excel = [str(row.get('ORDEN') or row.get('OF')).strip() for _, row in df.iterrows() if pd.notna(row.get('ORDEN') or row.get('OF'))]
        
        # Consultar cuáles ya existen en la DB 2012
        with connections['externa_2012'].cursor() as cursor:
            cursor.execute("SELECT OF_Stamping FROM CigarRings2012")
            ofs_db = set(str(row[0]).strip() for row in cursor.fetchall())
        
        # Encontrar una que esté en Excel pero NO en DB
        for of in ofs_excel:
            if of.isdigit() and of not in ofs_db:
                return of
        return None
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    print(find_missing_of())
