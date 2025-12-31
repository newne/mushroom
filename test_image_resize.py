#!/usr/bin/env python3
"""
Test script for image resizing functionality
"""
import sys
sys.path.append('src')

from PIL import Image
from utils.mushroom_image_encoder import create_mushroom_encoder

def test_image_resizing():
    """Test the image resizing functionality"""
    try:
        # Initialize encoder
        encoder = create_mushroom_encoder()
        print('✅ Encoder initialized successfully')

        # Test image resizing method with different sizes
        test_cases = [
            (1920, 1080, "Large landscape image"),
            (1080, 1920, "Large portrait image"), 
            (800, 600, "Small landscape image"),
            (600, 800, "Small portrait image"),
            (960, 540, "Target size image"),
            (500, 300, "Very small image")
        ]

        for width, height, description in test_cases:
            test_image = Image.new('RGB', (width, height), color='red')
            print(f'\n{description}:')
            print(f'  Original size: {test_image.size}')
            
            resized_image = encoder._resize_image_for_llama(test_image)
            print(f'  Resized size: {resized_image.size}')
            
            # Verify the short side is 960 or the image wasn't enlarged
            resized_width, resized_height = resized_image.size
            short_side = min(resized_width, resized_height)
            
            if width <= 960 and height <= 960:
                # Small images should not be enlarged
                assert resized_image.size == test_image.size, f"Small image was enlarged: {test_image.size} -> {resized_image.size}"
                print(f'  ✅ Small image kept original size')
            else:
                # Large images should have short side = 960
                assert short_side == 960, f"Short side should be 960, got {short_side}"
                print(f'  ✅ Large image resized correctly, short side = {short_side}')

        print('\n✅ All image resizing tests passed!')
        return True

    except Exception as e:
        print(f'❌ Test failed: {e}')
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_image_resizing()
    sys.exit(0 if success else 1)