import pandas as pd
import os

excel_path = r"C:\Users\Eugenio\OneDrive\Desktop\Cigar_rings\CR PLANNING.xlsm"

def get_sample_row():
    try:
        xls = pd.ExcelFile(excel_path, engine='openpyxl')
        # Intentar con STAMPING primero
        sheet = 'STAMPING' if 'STAMPING' in xls.sheet_names else xls.sheet_names[0]
        
        # Leer las primeras filas para encontrar el encabezado
        df_preview = pd.read_excel(excel_path, engine='openpyxl', sheet_name=sheet, nrows=10, header=None)
        header_row = 0
        for index, row in df_preview.iterrows():
            if any(str(val).strip().upper() == "ORDEN" for val in row):
                header_row = index
                break
        
        df = pd.read_excel(excel_path, engine='openpyxl', sheet_name=sheet, header=header_row)
        df.columns = [str(c).strip().upper() for c in df.columns]
        
        # Buscar una fila con OF válida
        for _, row in df.iterrows():
            of_val = row.get('ORDEN') or row.get('OF')
            if pd.notna(of_val) and str(of_val).strip().isdigit():
                return {
                    'of': str(of_val).strip(),
                    'proceso': sheet,
                    'cliente': row.get('CLIENTE', '—'),
                    'referencia': row.get('REFERENCIA', '—')
                }
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    print(get_sample_row())
