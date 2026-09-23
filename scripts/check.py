from pathlib import Path

import nbformat


notebook_path = Path(__file__).resolve().parents[1] / "reports/project_analysis.ipynb"
notebook = nbformat.read(notebook_path, as_version=4)
nbformat.validate(notebook)
code_cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
assert code_cells, "Published notebook has no code cells"
assert all(cell.execution_count is not None for cell in code_cells), "Notebook has unexecuted code cells"
assert not any(output.output_type == "error" for cell in code_cells for output in cell.outputs), "Notebook contains an error output"
print(f"Validated published notebook: {len(code_cells)} executed code cells")
