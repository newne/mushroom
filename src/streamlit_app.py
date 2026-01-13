"""
@Project ：get_data
@File    ：streamlit_app.py
@IDE     ：PyCharm
@Author  ：niucg1@lenovo.com
@Date    ：2024/10/16 14:56
@Desc     :
"""

import streamlit as st
def test():
    print('True')

def main():
    pg = st.navigation(
        [
            st.Page(test, title="💡 cop-current"),
        ]
    )
    pg.run()


if __name__ == "__main__":
    main()
