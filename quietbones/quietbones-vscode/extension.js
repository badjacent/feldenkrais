const vscode = require('vscode');
const { execFile } = require('child_process');
const path = require('path');
const fs = require('fs');

/**
 * Find regen.py by walking up from the active file looking for quietbones/regen.py,
 * or use the configured path.
 */
function findRegenScript(activeFilePath) {
    const configured = vscode.workspace.getConfiguration('quietbones').get('regenScript');
    if (configured && fs.existsSync(configured)) return configured;

    // Walk up from the file looking for regen.py as a sibling
    let dir = path.dirname(activeFilePath);
    for (let i = 0; i < 5; i++) {
        const candidate = path.join(dir, 'regen.py');
        if (fs.existsSync(candidate)) return candidate;
        dir = path.dirname(dir);
    }

    // Try workspace folders
    for (const folder of vscode.workspace.workspaceFolders || []) {
        const candidate = path.join(folder.uri.fsPath, 'quietbones', 'regen.py');
        if (fs.existsSync(candidate)) return candidate;
    }

    return null;
}

/**
 * Find Python executable.
 */
function findPython() {
    const configured = vscode.workspace.getConfiguration('quietbones').get('pythonPath');
    if (configured && fs.existsSync(configured)) return configured;

    // Try .venv in workspace
    for (const folder of vscode.workspace.workspaceFolders || []) {
        const candidate = path.join(folder.uri.fsPath, '.venv', 'bin', 'python');
        if (fs.existsSync(candidate)) return candidate;
    }

    return 'python3';
}

function activate(context) {
    const disposable = vscode.commands.registerCommand('quietbones.regenerate', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showErrorMessage('No active editor');
            return;
        }

        const selection = editor.selection;
        if (selection.isEmpty) {
            vscode.window.showErrorMessage('Select the lines you want to regenerate');
            return;
        }

        const regenScript = findRegenScript(editor.document.uri.fsPath);
        if (!regenScript) {
            vscode.window.showErrorMessage('Could not find regen.py');
            return;
        }

        const pythonPath = findPython();

        // Ask for reason (optional)
        const reason = await vscode.window.showInputBox({
            prompt: 'Reason for regeneration (optional)',
            placeHolder: 'e.g., transition feels abrupt, too instructive, add more space...',
        });

        if (reason === undefined) return; // cancelled

        const startLine = selection.start.line + 1; // 1-indexed
        const endLine = selection.end.line + 1;
        const filePath = editor.document.uri.fsPath;

        const request = JSON.stringify({
            file: filePath,
            start: startLine,
            end: endLine,
            reason: reason || '',
        });

        // Show progress
        await vscode.window.withProgress(
            {
                location: vscode.ProgressLocation.Notification,
                title: 'QuietBones: Regenerating...',
                cancellable: false,
            },
            () => new Promise((resolve, reject) => {
                const proc = execFile(
                    pythonPath,
                    [regenScript, '--json'],
                    {
                        cwd: path.dirname(regenScript),
                        env: { ...process.env },
                        maxBuffer: 1024 * 1024,
                    },
                    (error, stdout, stderr) => {
                        if (error) {
                            vscode.window.showErrorMessage(
                                `Regeneration failed: ${stderr || error.message}`
                            );
                            reject(error);
                            return;
                        }

                        try {
                            const result = JSON.parse(stdout);
                            const replacement = result.replacement;

                            // Show diff in a side-by-side view
                            const originalText = editor.document.getText(
                                new vscode.Range(
                                    new vscode.Position(selection.start.line, 0),
                                    new vscode.Position(
                                        selection.end.line,
                                        editor.document.lineAt(selection.end.line).text.length
                                    )
                                )
                            );

                            // Show replacement in an untitled document for review
                            showReplacementPreview(
                                editor, selection, originalText, replacement
                            );
                            resolve();
                        } catch (e) {
                            vscode.window.showErrorMessage(`Failed to parse response: ${e.message}`);
                            reject(e);
                        }
                    }
                );

                proc.stdin.write(request);
                proc.stdin.end();
            })
        );
    });

    context.subscriptions.push(disposable);
}

async function showReplacementPreview(editor, selection, original, replacement) {
    const action = await vscode.window.showInformationMessage(
        'Regeneration complete. Apply replacement?',
        { modal: false },
        'Apply',
        'Preview',
        'Discard'
    );

    if (action === 'Apply') {
        await editor.edit(editBuilder => {
            const range = new vscode.Range(
                new vscode.Position(selection.start.line, 0),
                new vscode.Position(
                    selection.end.line,
                    editor.document.lineAt(selection.end.line).text.length
                )
            );
            editBuilder.replace(range, replacement);
        });
    } else if (action === 'Preview') {
        // Show in a new tab for comparison
        const doc = await vscode.workspace.openTextDocument({
            content: `=== ORIGINAL (lines ${selection.start.line + 1}-${selection.end.line + 1}) ===\n\n${original}\n\n=== REPLACEMENT ===\n\n${replacement}`,
            language: 'markdown',
        });
        await vscode.window.showTextDocument(doc, vscode.ViewColumn.Beside);

        // Ask again after preview
        const finalAction = await vscode.window.showInformationMessage(
            'Apply this replacement?',
            'Apply',
            'Discard'
        );
        if (finalAction === 'Apply') {
            await editor.edit(editBuilder => {
                const range = new vscode.Range(
                    new vscode.Position(selection.start.line, 0),
                    new vscode.Position(
                        selection.end.line,
                        editor.document.lineAt(selection.end.line).text.length
                    )
                );
                editBuilder.replace(range, replacement);
            });
        }
    }
    // 'Discard' or dismissed — do nothing
}

function deactivate() {}

module.exports = { activate, deactivate };
