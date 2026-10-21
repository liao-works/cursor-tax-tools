#!/usr/bin/env python3
"""
Create Excel template for batch processing
"""
import os
import pandas as pd

def create_template():
    """Create batch rate template Excel file"""
    # Ensure templates directory exists
    os.makedirs('templates', exist_ok=True)
    
    # Create template DataFrame with sample data
    template_data = {
        'code': [
            '0101210000',
            '0201100000', 
            '0301100000',
            '0401100000',
            '0501100000'
        ]
    }
    
    df = pd.DataFrame(template_data)
    
    # Save template
    template_path = 'templates/batch_rate_template.xlsx'
    with pd.ExcelWriter(template_path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
        # Set code column as text format
        worksheet = writer.sheets['Sheet1']
        for cell in worksheet['A']:
            cell.number_format = '@'
    
    print(f"Template created: {template_path}")

if __name__ == '__main__':
    create_template()
