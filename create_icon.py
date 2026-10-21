#!/usr/bin/env python3
"""
Create application icon
"""
from PIL import Image, ImageDraw, ImageFont
import os

def create_icon():
    """Create a simple application icon"""
    # Create a 256x256 image
    size = (256, 256)
    img = Image.new('RGBA', size, (70, 130, 180, 255))  # Steel blue background
    
    # Create drawing context
    draw = ImageDraw.Draw(img)
    
    # Draw a simple calculator/tax icon (simplified representation)
    # Draw a circle for the main shape
    circle_center = (128, 128)
    circle_radius = 100
    draw.ellipse(
        [(circle_center[0] - circle_radius, circle_center[1] - circle_radius),
         (circle_center[0] + circle_radius, circle_center[1] + circle_radius)],
        fill=(255, 255, 255, 255),
        outline=(0, 0, 0, 255),
        width=3
    )
    
    # Draw a simple £ symbol
    try:
        # Try to use a system font
        font = ImageFont.truetype("arial.ttf", 120)
    except:
        # Fallback to default font
        font = ImageFont.load_default()
    
    # Draw £ symbol
    draw.text((128, 128), "£", fill=(70, 130, 180, 255), anchor="mm", font=font)
    
    # Save as ICO file
    img.save('app.ico', format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
    
    print("Icon created: app.ico")

if __name__ == '__main__':
    create_icon()
