#include <QApplication>
#include <QPushButton>
#include <QMessageBox>
#include <QVBoxLayout>
#include <QWidget>
int main(int argc, char **argv) {
    QApplication app(argc, argv);
    QWidget w; w.setWindowTitle("B:");
    auto *lay = new QVBoxLayout(&w);
    auto *b = new QPushButton("Delete x.pdf"); lay->addWidget(b);
    QObject::connect(b, &QPushButton::clicked, [&]() {
        QMessageBox box(QMessageBox::Question, "Confirm", "Delete x.pdf?", QMessageBox::NoButton, &w);
        auto *keep = box.addButton("Keep", QMessageBox::RejectRole);
        auto *del = box.addButton("Delete", QMessageBox::AcceptRole);
        box.exec();
        w.setWindowTitle(box.clickedButton() == del ? "B:done deleted" : "B:done kept");
        (void)keep;  // the Keep button exists for the a11y tree
    });
    w.resize(500, 300); w.show();
    return app.exec();
}
