import pandas as pd
excel_path = r"C:\Users\Eugenio\OneDrive\Desktop\Cigar_rings\CR PLANNING.xlsm"

def list_embossing_columns():
    try:
        xls = pd.ExcelFile(excel_path, engine='openpyxl')
        if 'EMBOSSING' in xls.sheet_names:
            df_preview = pd.read_excel(excel_path, engine='openpyxl', sheet_name='EMBOSSING', nrows=10, header=None)
            header_row = 0
            for index, row in df_preview.iterrows():
                if any(str(val).strip().upper() == "ORDEN" for val in row):
                    header_row = index
                    break
            df = pd.read_excel(excel_path, engine='openpyxl', sheet_name='EMBOSSING', header=header_row)
            return list(df.columns)
        return "Sheet EMBOSSING not found"
    except Exception as e:
        return str(e)

if __name__ == "__main__":
    print(list_embossing_columns())
