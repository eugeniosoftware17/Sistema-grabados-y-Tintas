import pandas as pd
from decouple import config

excel_path = config('PLANI_EXCEL_PATH')

def list_excel_columns():
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
        return list(df.columns)
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    print(list_excel_columns())
