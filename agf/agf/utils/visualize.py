import matplotlib.pyplot as plt
import tempfile
import os
import subprocess


def visualize_graph(graph):
    mermaid = graph.get_graph().draw_mermaid()
    print(mermaid)  # Keep the original print for reference
    
    # Create a temporary file to store the Mermaid content
    with tempfile.NamedTemporaryFile(suffix='.mmd', mode='w', delete=False) as f:
        f.write(mermaid)
        mmd_file = f.name
    
    # Create output file path for the PNG
    png_file = mmd_file.replace('.mmd', '.png')
    
    try:
        # Convert Mermaid to PNG using mmdc (requires mermaid-cli to be installed)
        subprocess.run(['mmdc', '-i', mmd_file, '-o', png_file], check=True)
        
        # Display the image using matplotlib
        img = plt.imread(png_file)
        plt.figure(figsize=(12, 8))
        plt.imshow(img)
        plt.axis('off')
        plt.show()
    except subprocess.CalledProcessError:
        print("Error: Failed to render Mermaid diagram. Please ensure mermaid-cli (mmdc) is installed.")
        print("You can install it using: npm install -g @mermaid-js/mermaid-cli")
    finally:
        # Clean up temporary files
        os.unlink(mmd_file)
        if os.path.exists(png_file):
            os.unlink(png_file)
